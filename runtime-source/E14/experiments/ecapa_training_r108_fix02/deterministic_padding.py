"""Same reflection padding via slices/flip/cat, with deterministic CUDA backward.

No parameters, state keys, kernel shape or padding values change. R4 stays untouched.
"""
import torch
from speechbrain.lobes.models.ECAPA_TDNN import Conv1d
from speechbrain.nnet.CNN import get_padding_elem


def reflect_pad(x,left,right):
    if x.ndim!=3 or min(left,right)<0 or max(left,right)>=x.shape[-1]:
        raise ValueError('Reflection padding must be smaller than time dimension')
    n=x.shape[-1]
    return torch.cat((x[...,1:left+1].flip(-1),x,x[...,n-right-1:n-1].flip(-1)),dim=-1)


class DeterministicConv1d(Conv1d):
    def _manage_padding(self,x,kernel_size,dilation,stride):
        if self.padding_mode!='reflect':return super()._manage_padding(x,kernel_size,dilation,stride)
        left,right=get_padding_elem(self.in_channels,stride,kernel_size,dilation)
        return reflect_pad(x,left,right)


def adapt_reflection(encoder):
    count=0
    for module in encoder.modules():
        if isinstance(module,Conv1d) and not isinstance(module,DeterministicConv1d) and module.padding_mode=='reflect':
            # Parameterless implementation adapter preserves object, tensors and state_dict keys.
            module.__class__=DeterministicConv1d;count+=1
    return count
