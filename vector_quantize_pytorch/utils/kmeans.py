import torch
from einops import rearrange, repeat
from torch import Tensor, cdist
from torch.nn import CosineSimilarity, Identity

from vector_quantize_pytorch.utils.general import batched_sample_vectors, noop
from vector_quantize_pytorch.utils.losses import l2norm


def batched_bincount(x, *, minlength):
    batch, dtype, device = x.shape[0], x.dtype, x.device
    target = torch.zeros(batch, minlength, dtype=dtype, device=device)
    values = torch.ones_like(x)
    target.scatter_add_(-1, x, values)
    return target


def kmeans(
    samples: Tensor,
    num_clusters: int,
    num_iters: int = 10,
    use_cosine_sim=False,
    sample_fn=batched_sample_vectors,
    all_reduce_fn=noop,
):
    num_codebooks, dim, dtype = (
        samples.shape[0],
        samples.shape[-1],
        samples.dtype,
    )

    means = sample_fn(samples, num_clusters)
    if use_cosine_sim:
        dist_fn = CosineSimilarity(dim=-1)
        reg_fn = l2norm
    else:

        def dist_fn(x, y):
            return -cdist(x, y)

        reg_fn = Identity()

    for _ in range(num_iters):
        distances = dist_fn(samples, means)

        buckets = torch.argmax(distances, dim=-1)
        bins = batched_bincount(buckets, minlength=num_clusters)
        all_reduce_fn(bins)

        zero_mask = bins == 0
        bins_min_clamped = bins.masked_fill(zero_mask, 1)

        new_means = buckets.new_zeros(num_codebooks, num_clusters, dim, dtype=dtype)

        new_means.scatter_add_(1, repeat(buckets, "h n -> h n d", d=dim), samples)
        new_means = new_means / rearrange(bins_min_clamped, "... -> ... 1")
        all_reduce_fn(new_means)

        # if use_cosine_sim:
        #     new_means = l2norm(new_means)
        new_means = reg_fn(new_means)

        means = torch.where(rearrange(zero_mask, "... -> ... 1"), means, new_means)

    return means, bins
