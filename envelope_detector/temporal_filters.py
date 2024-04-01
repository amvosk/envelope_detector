import math
import torch
import torch.nn as nn
import torch.nn.functional as F
# import einops

from .layers import HilbertLayer


class Filter1D(nn.Module):
    def __init__(self, n_channels, kernel_size, srate, fmin_init, fmax_init, freq=None, bandwidth=None, padding_mode='zeros', seed=None):
        super().__init__()
        self.n_channels = n_channels
        self.kernel_size = kernel_size
        self.srate = srate
        
        self.padding = kernel_size // 2
        if padding_mode == 'zeros':
            self.pad = nn.ConstantPad1d(2*self.padding, 0)
        elif padding_mode == 'reflect':
            self.pad = nn.ReflectionPad1d(2*self.padding)
        
        self.register_buffer('_scale', torch.arange(-self.kernel_size//2 + 1, self.kernel_size//2 + 1).reshape((1,1,-1)) / self.srate)

        self.freq = freq
        if self.freq is None:
            coef_freq = self._create_parameters_freq(self.n_channels, fmin_init, fmax_init, seed=seed)
            self.coef_freq = nn.Parameter(coef_freq)
        else:
            self.register_buffer('_freq', freq)
        
        self.bandwidth = bandwidth
        if self.bandwidth is None:
            coef_bandwidth = self._create_parameters_bandwidth(self.n_channels, seed=seed)
            self.coef_bandwidth = nn.Parameter(coef_bandwidth)
        else:
            if not isinstance(bandwidth, torch.Tensor):
                bandwidth = torch.tensor(bandwidth, dtype=torch.float32).reshape((1,))
            assert bandwidth.shape[0] in (1, self.n_channels)
            if bandwidth.shape[0] != self.n_channels:
                bandwidth = bandwidth.repeat(self.n_channels)
            self.register_buffer('_bandwidth', bandwidth)
        
    def _create_parameters_freq(self, n_coef, fmin_init, fmax_init, seed=None):
        if seed is not None:
            torch.manual_seed(seed)
        coef = fmin_init + torch.rand(size=(n_coef,)) * (fmax_init - fmin_init)
        return coef
    
    def _create_parameters_bandwidth(self, n_coef, seed=None):
        if seed is not None:
            torch.manual_seed(seed)
        coef = torch.rand(size=(n_coef,)) * 0.8 + 0.1
        coef = torch.log(coef / (1-coef))
        return coef
    
    def _create_frequencies(self):
        if self.freq is None:
            freq = F.softplus(self.coef_freq)
        else:
            freq = self._freq
            
        if self.bandwidth is None:
            bandwidth = torch.sigmoid(self.coef_bandwidth)
        else:
            bandwidth = self._bandwidth
        bandwidth = bandwidth * freq
        
        freq_low = freq - bandwidth / 2
        freq_high = freq + bandwidth / 2

        return freq, bandwidth, freq_low, freq_high
    
    
class SincLayer(Filter1D):
    def __init__(self, n_channels, kernel_size, srate, fmin_init, fmax_init, freq=None, bandwidth=None, padding_mode='zeros', seed=None):
        super().__init__(n_channels, kernel_size, srate, fmin_init, fmax_init, freq, bandwidth, padding_mode, seed)
        
        self.register_buffer('_hamming_window', torch.hamming_window(kernel_size).reshape((1,1,-1)))
        self.hilbert = HilbertLayer()

    def _create_filters(self, freq_low, freq_high):
        freq_low, freq_high = freq_low.reshape((-1,1,1)), freq_high.reshape((-1,1,1))   
        filt_low = freq_low * torch.special.sinc(2 * freq_low * self._scale)
        filt_high = freq_high * torch.special.sinc(2 * freq_high * self._scale)
        filt = self._hamming_window * 2 * (filt_high - filt_low) / self.srate
        return filt
        
    def forward(self, x):
        ndim = x.ndim
        if ndim == 1:
            x = x.unsqueeze(0).unsqueeze(0)
        elif ndim == 2:
            x = x.unsqueeze(0)
            
        x = self.pad(x)
        _, _, freq_low, freq_high = self._create_frequencies()
        filt = self._create_filters(freq_low, freq_high)
        x = F.conv1d(x, filt, groups=x.shape[-2], padding='valid')
        
        if ndim == 1:
            x = x.squeeze(0).squeeze(0)
        elif ndim == 2:
            x = x.squeeze(0)
            
        x = x[...,self.padding:-self.padding]
        return x
    
    
class SincHilbertLayer(Filter1D):
    def __init__(self, n_channels, kernel_size, srate, fmin_init, fmax_init, freq=None, bandwidth=None, padding_mode='zeros', seed=None):
        super().__init__(n_channels, kernel_size, srate, fmin_init, fmax_init, freq, bandwidth, padding_mode, seed)
        
        self.register_buffer('_hamming_window', torch.hamming_window(kernel_size).reshape((1,1,-1)))
        self.hilbert = HilbertLayer()

    def _create_filters(self, freq_low, freq_high):
        freq_low, freq_high = freq_low.reshape((-1,1,1)), freq_high.reshape((-1,1,1))   
        filt_low = freq_low * torch.special.sinc(2 * freq_low * self._scale)
        filt_high = freq_high * torch.special.sinc(2 * freq_high * self._scale)
        filt = self._hamming_window * 2 * (filt_high - filt_low) / self.srate
        return filt
        
    def forward(self, x, return_filtered=False):
        ndim = x.ndim
        if ndim == 1:
            x = x.unsqueeze(0).unsqueeze(0)
        elif ndim == 2:
            x = x.unsqueeze(0)
            
        x = self.pad(x)
        _, _, freq_low, freq_high = self._create_frequencies()
        filt = self._create_filters(freq_low, freq_high)
        x = F.conv1d(x, filt, groups=x.shape[-2], padding='valid')
        
        if ndim == 1:
            x = x.squeeze(0).squeeze(0)
        elif ndim == 2:
            x = x.squeeze(0)
            
        if return_filtered:
            x = x[...,self.padding:-self.padding]
            return x
        else:
            x = self.hilbert(x)
            x = x[...,self.padding:-self.padding]
            x = torch.abs(x)
            return x


class WaveletLayer(Filter1D):
    def __init__(self, n_channels, kernel_size, srate, fmin_init, fmax_init, freq=None, bandwidth=None, padding_mode='zeros', seed=None):
        super().__init__(n_channels, kernel_size, srate, fmin_init, fmax_init, freq, bandwidth, padding_mode, seed)
           
    def _create_filters(self, freq, bandwidth):
        freq, bandwidth = freq.reshape((-1,1,1)), bandwidth.reshape((-1,1,1))
        sigma2 = (2 * math.log(2)) / (bandwidth * math.pi)**2
        filt = (2 * math.pi * sigma2)**(-1/2) / (self.srate / 2)
        filt = filt * torch.cos(2*math.pi * freq * self._scale)
        filt = filt * torch.exp(- self._scale**2 / (2 * sigma2))
        return filt
                            
    def forward(self, x):
        ndim = x.ndim
        if ndim == 1:
            x = x.unsqueeze(0).unsqueeze(0)
        elif ndim == 2:
            x = x.unsqueeze(0)
            
        x = self.pad(x)
        freq, bandwidth, _, _ = self._create_frequencies()
        filt = self._create_filters(freq, bandwidth)
        x = F.conv1d(x, filt, groups=x.shape[-2], padding='valid')
                            
        x = x[...,self.padding:-self.padding]
            
        if ndim == 1:
            x = x.squeeze(0).squeeze(0)
        elif ndim == 2:
            x = x.squeeze(0)
        return x

        
        
class ComplexWaveletLayer(Filter1D):
    def __init__(self, n_channels, kernel_size, srate, fmin_init, fmax_init, freq=None, bandwidth=None, padding_mode='zeros', seed=None):
        super().__init__(n_channels, kernel_size, srate, fmin_init, fmax_init, freq, bandwidth, padding_mode, seed)
           
    def _create_filters(self, freq, bandwidth):
        freq, bandwidth = freq.reshape((-1,1,1)), bandwidth.reshape((-1,1,1))
        sigma2 = (2 * math.log(2)) / (bandwidth * math.pi)**2
        filt = (2 * math.pi * sigma2)**(-1/2) / (self.srate / 2)
        filt = filt * (torch.exp(1j*2*math.pi * freq * self._scale) - torch.exp(-0.5*(2*math.pi * freq)**2))
        filt = filt * torch.exp(- self._scale**2 / (2 * sigma2))
        return filt
          
    def forward(self, x, return_filtered=False):
        ndim = x.ndim
        if ndim == 1:
            x = x.unsqueeze(0).unsqueeze(0)
        elif ndim == 2:
            x = x.unsqueeze(0)
            
        x = self.pad(x)
        x = x.to(torch.complex64)
        freq, bandwidth, _, _ = self._create_frequencies()
        filt = self._create_filters(freq, bandwidth)
        x = F.conv1d(x, filt, groups=x.shape[-2], padding='valid')
                            
        x = x[...,self.padding:-self.padding]
            
        if ndim == 1:
            x = x.squeeze(0).squeeze(0)
        elif ndim == 2:
            x = x.squeeze(0)
            
        if return_filtered:
            return torch.real(x)
        else:
            return torch.abs(x)
    
    





# class SincFilter1D(nn.Module):
#     def __init__(self, n_channels, kernel_size, srate, fmin_init, fmax_init, bandwidth=None, seed=None):
#         super().__init__()
#         self.n_channels = n_channels
#         self.kernel_size = kernel_size
#         self.bandwidth = bandwidth
#         self.srate = srate
                
#         self.register_buffer('_hamming_window', torch.hamming_window(self.kernel_size).reshape((1,1,-1)))
#         self.register_buffer('_scale', torch.arange(-self.kernel_size//2 + 1, self.kernel_size//2 + 1).reshape((1,1,-1)) / self.srate)

#         coef_freq = self._create_parameters_freq(self.n_channels, fmin_init, fmax_init, srate=self.srate, seed=seed)
#         self.coef_freq = nn.Parameter(coef_freq)
        
#         self.bandwidth = bandwidth
#         if bandwidth is None:
#             coef_bandwidth = self._create_parameters_bandwidth(self.n_channels, seed=seed)
#             self.coef_bandwidth = nn.Parameter(coef_bandwidth)
        

#     def _create_parameters_freq(self, n_coef, fmin_init, fmax_init, srate, seed=None):
#         if seed is not None:
#             torch.manual_seed(seed)
#         coef = fmin_init + torch.rand(size=(n_coef,)) * (fmax_init - fmin_init)
#         return coef
    
#     def _create_parameters_bandwidth(self, n_coef, seed=None):
#         if seed is not None:
#             torch.manual_seed(seed)
#         coef = torch.rand(size=(n_coef,)) * 0.8 + 0.1
#         coef = torch.log(coef / (1-coef))
#         return coef
    
#     def create_frequencies(self, freq=None, bandwidth=None):
#         if freq is None:
#             freq = F.softplus(self.coef_freq)
#         if bandwidth is None:
#             bandwidth = torch.sigmoid(self.coef_bandwidth)
#         if not isinstance(bandwidth, torch.Tensor):
#             bandwidth = torch.tensor(bandwidth, dtype=torch.float32).repeat(*freq.shape)

#         freq_low = (freq * (1 - bandwidth / 2)).reshape((-1,1,1))
#         freq_high = (freq * (1 + bandwidth / 2)).reshape((-1,1,1))
#         return freq, bandwidth, freq_low, freq_high

#     def create_filters(self, freq_low=None, freq_high=None):
#         if freq_low is None or freq_high is None:
#             _, _, freq_low, freq_high = self.create_frequencies(bandwidth=self.bandwidth)
#         filt_low = freq_low * torch.special.sinc(2 * freq_low * self._scale)
#         filt_high = freq_high * torch.special.sinc(2 * freq_high * self._scale)
#         filt = self._hamming_window * 2 * (filt_high - filt_low) / self.srate
#         return filt
    
#     def forward(self, x):
#         filt = self.create_filters()
#         x = F.conv1d(x, filt, groups=x.shape[-2], padding='valid')
#         return x
    
    
# class FilterHilbertLayer(nn.Module):
#     def __init__(self, n_channels, kernel_size, srate, fmin_init, fmax_init, bandwidth, padding_mode='zeros', seed=None):
#         super().__init__()
        
#         self.padding = kernel_size // 2
#         if padding_mode == 'zeros':
#             self.pad = nn.ConstantPad1d(2*self.padding, 0)
#         elif padding_mode == 'reflect':
#             self.pad = nn.ReflectionPad1d(2*self.padding)
            
#         self.temporal_filter = SincFilter1D(n_channels, kernel_size, srate, fmin_init, fmax_init, bandwidth, seed=seed)
#         self.hilbert = HilbertLayer()

#     def create_frequencies(self, freq=None, bandwidth=None):
#         return self.temporal_filter.create_frequencies(freq, bandwidth)
                            
#     def create_filters(self, freq=None, bandwidth=None):
#         return self.temporal_filter.create_filters(freq, bandwidth)
        
#     def forward(self, x, return_filtered=False):
#         ndim = x.ndim
#         if ndim == 1:
#             x = x.unsqueeze(0).unsqueeze(0)
#         elif ndim == 2:
#             x = x.unsqueeze(0)
            
#         x = self.pad(x)
#         x = self.temporal_filter(x)
#         x = x[...,self.padding:-self.padding]
            
#         if ndim == 1:
#             x = x.squeeze(0).squeeze(0)
#         elif ndim == 2:
#             x = x.squeeze(0)
            
#         if return_filtered:
#             return x
        
#         x = self.hilbert(x)
#         x_abs = torch.abs(x)
#         return x_abs
    
    
# class ComplexWaveletLayer(nn.Module):
#     def __init__(self, n_channels, kernel_size, srate, fmin_init, fmax_init, bandwidth=None, padding_mode='zeros', seed=None):
#         super().__init__()
#         self.n_channels = n_channels
#         self.kernel_size = kernel_size
#         self.bandwidth = bandwidth
#         self.srate = srate
        
#         self.padding = kernel_size // 2
#         if padding_mode == 'zeros':
#             self.pad = nn.ConstantPad1d(2*self.padding, 0)
#         elif padding_mode == 'reflect':
#             self.pad = nn.ReflectionPad1d(2*self.padding)
        
#         self.register_buffer('_scale', torch.arange(-self.kernel_size//2 + 1, self.kernel_size//2 + 1).reshape((1,1,-1)) / self.srate)

#         coef_freq = self._create_parameters_freq(self.n_channels, fmin_init, fmax_init, srate=self.srate, seed=seed)
#         self.coef_freq = nn.Parameter(coef_freq)
        
#         self.bandwidth = bandwidth
#         if bandwidth is None:
#             coef_bandwidth = self._create_parameters_bandwidth(self.n_channels, seed=seed)
#             self.coef_bandwidth = nn.Parameter(coef_bandwidth)

        
#     def _create_parameters_freq(self, n_coef, fmin_init, fmax_init, srate, seed=None):
#         if seed is not None:
#             torch.manual_seed(seed)
#         coef = fmin_init + torch.rand(size=(n_coef,)) * (fmax_init - fmin_init)
#         return coef

#     def _create_parameters_bandwidth(self, n_coef, seed=None):
#         if seed is not None:
#             torch.manual_seed(seed)
#         coef = torch.rand(size=(n_coef,)) * 0.8 + 0.1
#         coef = torch.log(coef / (1-coef))
#         return coef
    
#     def create_frequencies(self, freq=None, bandwidth=None):
#         if freq is None:
#             freq = F.softplus(self.coef_freq)
#         if bandwidth is None:
#             bandwidth = torch.sigmoid(self.coef_bandwidth)
#         if not isinstance(bandwidth, torch.Tensor):
#             bandwidth = torch.tensor(bandwidth, dtype=torch.float32).repeat(*freq.shape)

#         freq_low = (freq * (1 - bandwidth / 2)).reshape((-1,1,1))
#         freq_high = (freq * (1 + bandwidth / 2)).reshape((-1,1,1))
#         return freq, bandwidth, freq_low, freq_high
                            
#     def create_filters(self, freq=None, bandwidth=None):
#         freq, bandwidth, _, _ = self.create_frequencies(freq, bandwidth)
#         freq = freq.reshape((-1,1,1))
#         bandwidth_ = bandwidth.reshape((-1,1,1)) * freq
        
#         sigma2 = (2 * math.log(2)) / (bandwidth_ * math.pi)**2
#         filt = (2 * math.pi * sigma2)**(-1/2) / (self.srate / 2)
#         filt = filt * (torch.exp(1j*2*math.pi * freq * self._scale) - torch.exp(-0.5*(2*math.pi * freq)**2))
#         filt = filt * torch.exp(- self._scale**2 / (2 * sigma2))
#         return filt

                            
#     def forward(self, x, return_filtered=False):
#         ndim = x.ndim
#         if ndim == 1:
#             x = x.unsqueeze(0).unsqueeze(0)
#         elif ndim == 2:
#             x = x.unsqueeze(0)
            
#         x = self.pad(x)
                            
#         x = x.to(torch.complex64)
#         filt = self.create_filters(bandwidth=self.bandwidth)
#         x = F.conv1d(x, filt, groups=x.shape[-2], padding='valid')
                            
#         x = x[...,self.padding:-self.padding]
            
#         if ndim == 1:
#             x = x.squeeze(0).squeeze(0)
#         elif ndim == 2:
#             x = x.squeeze(0)
            
#         if return_filtered:
#             return torch.real(x)
#         else:
#             return torch.abs(x)