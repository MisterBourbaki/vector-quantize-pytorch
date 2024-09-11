import torch.nn.functional as F
from torch import Tensor, einsum


def l2norm(t: Tensor) -> Tensor:
    """Returns the L2 normalization of the tensor t w.r.t. last dimension.

    Parameters
    ----------
    t : Tensor
        a torch Tensor of shape (*, D)

    Returns
    -------
    Tensor
        the L2 normalization of the input tensor, w.r.t last dimension.
        Shape of the output is the same as the input.
    """
    return F.normalize(t, p=2, dim=-1)


# def cdist(x, y):
#     x2 = reduce(x**2, "b n d -> b n", "sum")
#     y2 = reduce(y**2, "b n d -> b n", "sum")
#     xy = einsum("b i d, b j d -> b i j", x, y) * -2
#     return (
#         (rearrange(x2, "b i -> b i 1") + rearrange(y2, "b j -> b 1 j") + xy)
#         .clamp(min=0)
#         .sqrt()
#     )


def orthogonal_loss_fn(t):
    # eq (2) from https://arxiv.org/abs/2112.00384
    h, n = t.shape[:2]
    normed_codes = l2norm(t)
    cosine_sim = einsum("h i d, h j d -> h i j", normed_codes, normed_codes)
    return (cosine_sim**2).sum() / (h * n**2) - (1 / n)