import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
import torchaudio.transforms

import einops
import scipy.signal as sg

from .utils import SimpleDataset
from .layers import HilbertAmplitudeLayer, HilbertSplitLayer


class EnvelopeDetector(nn.Module):
    """
    An interpretable envelope detector for decoding and interpreting cortical signals.

    The class represents a PyTorch model that applies various filters,
    activation methods, and downsampling techniques to input data.

    Parameters:
    ----------
    nchannels : int
        Number of input channels.

    nfeatures : int
        Number of features after spatial filtering.

    temporal_filter_size : int, optional (default=7)
        Kernel size for the temporal convolution layer.

    downsample_coef : int, optional (default=1)
        Ratio between the input and output sampling rates.

    dropout : float, optional (default=0)
        Dropout rate after the temporal convolution layer.
        It's useful when the `temporal_filter_size` is large.

    activation : str, optional (default='demodulation')
        Specifies the activation method. Options include 'demodulation' which uses the absolute value,
        and 'hilbert' which employs the absolute value of the Hilbert transform.

    downsample_method : str, optional (default='avepool')
        Specifies the downsampling method. Options are:
        - 'avepool' which uses `nn.AvgPool1d`,
        - 'resample' which employs `torchaudio.transforms.Resample`,
        - 'none' which skips downsampling.

    fs_in : int, optional (default=1000)
        Sampling rate of the input data.

    use_temporal_smoother : bool, optional (default=False)
        Whether or not to use an additional temporal convolution layer after the activation layer.

    temporal_smoother_size : int, optional (default=3)
        Kernel size for the additional temporal convolution layer
        (only relevant if `use_temporal_smoother` is True).
    """

    def __init__(
        self,
        nchannels,
        nfeatures,
        spatial_bias=True,
        temporal_filter_enable=True,
        temporal_filter_size=7,
        temporal_filter_dilation=1,
        
        downsample_coef=1,
        dropout=0,
        activation="demodulation",
        downsample_method="avepool",
        fs_in=1000,
        use_temporal_smoother=False,
        temporal_smoother_size=3,
    ):
        super(EnvelopeDetector, self).__init__()

        # Properties
        self.nchannels = nchannels
        self.nfeatures = nfeatures
        self.temporal_filter_size = temporal_filter_size
        self.temporal_filter_dilation = temporal_filter_dilation
        self.fs_in = fs_in
        self.downsample_coef = downsample_coef

        # Spatial filtering
        self.spatial_filter = nn.Conv1d(
            nchannels, nfeatures, kernel_size=1, bias=spatial_bias
        )
        self.spatial_filter_batchnorm = nn.BatchNorm1d(nfeatures, affine=False)

        # Temporal filtering
        if temporal_filter_enable:
            self.temporal_filter = nn.Conv1d(
                nfeatures,
                nfeatures,
                kernel_size=temporal_filter_size,
                bias=False,
                groups=nfeatures,
                padding="same",
                dilation=temporal_filter_dilation,
            )
            self.temporal_filter_batchnorm = nn.BatchNorm1d(nfeatures, affine=False)
        else:
            self.temporal_filter = None
            self.temporal_filter_batchnorm = None
        
        # Dropout layer
        self.dropout_layer = nn.Dropout(p=dropout) if dropout else None

        # Activation function
        if callable(activation) and isinstance(activation(), torch.nn.Module):
            self.activation = activation()
        elif isinstance(activation, str):
            if activation == "demodulation":
                self.activation = nn.LeakyReLU(-1)
            elif activation == "hilbert_amplitude":
                self.activation = HilbertAmplitudeLayer()
            elif activation == "hilbert_split":
                self.activation = HilbertSplitLayer()
            else:
                self.activation = None
        else:
            self.activation = None

        # Temporal smoother
        self.temporal_smoother = (
            nn.Conv1d(
                nfeatures,
                nfeatures,
                kernel_size=temporal_smoother_size,
                groups=nfeatures,
                padding="same",
            )
            if use_temporal_smoother
            else None
        )

        # Downsampler
        if downsample_coef > 1:
            if downsample_method == "avepool":
                self.downsampler = nn.AvgPool1d(
                    kernel_size=downsample_coef, stride=downsample_coef
                )
            elif downsample_method == "resample":
                self.downsampler = torchaudio.transforms.Resample(
                    fs_in, fs_in / downsample_coef
                )
            elif downsample_method == "none":
                self.downsampler = None
        else:
            self.downsampler = None

    def get_spatial_filter(self):
        return einops.rearrange(self.spatial_filter.weight, 'o i 1 -> o i').clone().detach()
            
    def get_temporal_filter(self):
        if self.temporal_filter:
            temporal_filter = einops.rearrange(self.temporal_filter.weight, 'o 1 t -> o t')
            dilated_filter_size = 1 + (temporal_filter.shape[1] - 1) * self.temporal_filter_dilation
            dilated_filter = torch.zeros(
                (temporal_filter.shape[0], dilated_filter_size),
                dtype=self.temporal_filter.weight.dtype,
                device=self.temporal_filter.weight.device,
            )
            for i in range(temporal_filter.shape[1]):
                dilated_filter[:, i * self.temporal_filter_dilation] = temporal_filter[:, i]
        else:
            dilated_filter = torch.ones(
                size=(self.nfeatures, 1), 
                dtype=self.spatial_filter.weight.dtype,
                device=self.spatial_filter.weight.device,
            )
        return dilated_filter.clone().detach()

            
    def forward(self, x):
        # If input is 2D, add a singleton batch dimension
        if len(x.shape) == 2:
            x = einops.rearrange(x, "C L -> 1 C L")

        # Spatial filtering
        x = self.spatial_filter(x) if self.spatial_filter else x
        x = self.spatial_filter_batchnorm(x) if self.spatial_filter_batchnorm else x

        # Temporal filtering
        x = self.temporal_filter(x) if self.temporal_filter else x
        x = self.dropout_layer(x) if self.dropout_layer else x
        x = self.temporal_filter_batchnorm(x) if self.temporal_filter_batchnorm else x

        # Activation
        x = self.activation(x) if self.activation else x

        # Temporal smoothing
        x = self.temporal_smoother(x) if self.temporal_smoother else x

        # Downsampling
        x = self.downsampler(x) if self.downsampler else x

        return x
