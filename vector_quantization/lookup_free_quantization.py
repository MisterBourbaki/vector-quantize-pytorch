"""
Lookup Free Quantization
Proposed in https://arxiv.org/abs/2310.05737

In the simplest setup, each dimension is quantized into {-1, 1}.
An entropy penalty is used to encourage utilization.
"""

from collections import namedtuple
from functools import partial
from math import ceil, log2

import torch
import torch.nn.functional as F
from einops import pack, rearrange, reduce
from torch import einsum, nn
from torch.amp import autocast
from torch.nn import Module

from vector_quantization.utils.distributed import (
    maybe_distributed_mean,
)
from vector_quantization.utils.general import (
    entropy,
    exists,
    identity,
    unpack_one,
)
from vector_quantization.utils.losses import l2norm

Return = namedtuple("Return", ["quantized", "indices", "entropy_aux_loss"])

LossBreakdown = namedtuple(
    "LossBreakdown", ["per_sample_entropy", "batch_entropy", "commitment"]
)


class CosineSimLinear(Module):
    def __init__(self, dim_in, dim_out, scale=1.0):
        super().__init__()
        self.scale = scale
        self.weight = nn.Parameter(torch.randn(dim_in, dim_out))

    def forward(self, x):
        x = F.normalize(x, dim=-1)
        w = F.normalize(self.weight, dim=0)
        return (x @ w) * self.scale


class LFQ(Module):
    def __init__(
        self,
        *,
        dim=None,
        codebook_size=None,
        entropy_loss_weight=0.1,
        commitment_loss_weight=0.25,
        diversity_gamma=1.0,
        straight_through_activation=nn.Identity(),
        num_codebooks=1,
        keep_num_codebooks_dim=None,
        codebook_scale=1.0,  # for residual LFQ, codebook scaled down by 2x at each layer
        frac_per_sample_entropy=1.0,  # make less than 1. to only use a random fraction of the probs for per sample entropy
        has_projections=None,
        projection_has_bias=True,
        soft_clamp_input_value=None,
        cosine_sim_project_in=False,
        cosine_sim_project_in_scale=None,
        channel_first=False,
        experimental_softplus_entropy_loss=False,
        entropy_loss_offset=5.0,  # how much to shift the loss before softplus
        spherical=False,  # from https://arxiv.org/abs/2406.07548
    ):
        super().__init__()

        # some assert validations

        assert exists(dim) or exists(
            codebook_size
        ), "either dim or codebook_size must be specified for LFQ"
        assert (
            not exists(codebook_size) or log2(codebook_size).is_integer()
        ), f"your codebook size must be a power of 2 for lookup free quantization (suggested {2 ** ceil(log2(codebook_size))})"

        codebook_size = codebook_size if codebook_size is not None else 2**dim

        self.codebook_size = codebook_size

        codebook_dim = int(log2(codebook_size))
        codebook_dims = codebook_dim * num_codebooks
        dim = dim if dim is not None else codebook_dims

        has_projections = (
            has_projections if has_projections is not None else (dim != codebook_dims)
        )

        if cosine_sim_project_in:
            cosine_sim_project_in = (
                cosine_sim_project_in
                if cosine_sim_project_in is not None
                else codebook_scale
            )
            project_in_klass = partial(CosineSimLinear, scale=cosine_sim_project_in)
        else:
            project_in_klass = partial(nn.Linear, bias=projection_has_bias)

        self.project_in = (
            project_in_klass(dim, codebook_dims) if has_projections else nn.Identity()
        )
        self.project_out = (
            nn.Linear(codebook_dims, dim, bias=projection_has_bias)
            if has_projections
            else nn.Identity()
        )
        self.has_projections = has_projections

        self.dim = dim
        self.codebook_dim = codebook_dim
        self.num_codebooks = num_codebooks

        keep_num_codebooks_dim = (
            keep_num_codebooks_dim
            if keep_num_codebooks_dim is not None
            else (num_codebooks > 1)
        )
        assert not (num_codebooks > 1 and not keep_num_codebooks_dim)
        self.keep_num_codebooks_dim = keep_num_codebooks_dim

        # channel first

        self.channel_first = channel_first

        # straight through activation

        self.activation = straight_through_activation

        # whether to use BSQ (binary spherical quantization)

        self.spherical = spherical
        self.maybe_l2norm = (
            (lambda t: l2norm(t) * self.codebook_scale) if spherical else identity
        )

        # entropy aux loss related weights

        assert 0 < frac_per_sample_entropy <= 1.0
        self.frac_per_sample_entropy = frac_per_sample_entropy

        self.diversity_gamma = diversity_gamma
        self.entropy_loss_weight = entropy_loss_weight

        # codebook scale

        self.codebook_scale = codebook_scale

        # commitment loss

        self.commitment_loss_weight = commitment_loss_weight

        # whether to soft clamp the input value from -value to value

        self.soft_clamp_input_value = soft_clamp_input_value
        assert (
            not exists(soft_clamp_input_value)
            or soft_clamp_input_value >= codebook_scale
        )

        # whether to make the entropy loss positive through a softplus (experimental, please report if this worked or not in discussions)

        self.entropy_loss_offset = entropy_loss_offset
        self.experimental_softplus_entropy_loss = experimental_softplus_entropy_loss

        # for no auxiliary loss, during inference

        self.register_buffer("mask", 2 ** torch.arange(codebook_dim - 1, -1, -1))
        self.register_buffer("zero", torch.tensor(0.0), persistent=False)

        # codes

        all_codes = torch.arange(codebook_size)
        bits = ((all_codes[..., None].int() & self.mask) != 0).float()
        codebook = self.bits_to_codes(bits)

        self.register_buffer("codebook", codebook, persistent=False)

    def bits_to_codes(self, bits):
        return bits * self.codebook_scale * 2 - self.codebook_scale

    @property
    def dtype(self):
        return self.codebook.dtype

    def indices_to_codes(self, indices, project_out=True):
        should_transpose = self.channel_first

        if not self.keep_num_codebooks_dim:
            indices = rearrange(indices, "... -> ... 1")

        # indices to codes, which are bits of either -1 or 1

        bits = ((indices[..., None].int() & self.mask) != 0).to(self.dtype)

        codes = self.bits_to_codes(bits)

        codes = self.maybe_l2norm(codes)

        codes = rearrange(codes, "... c d -> ... (c d)")

        # whether to project codes out to original dimensions
        # if the input feature dimensions were not log2(codebook size)

        if project_out:
            codes = self.project_out(codes)

        # rearrange codes back to original shape

        if should_transpose:
            codes = rearrange(codes, "b ... d -> b d ...")

        return codes

    @autocast(device_type="cuda", enabled=False)
    def forward(
        self,
        x,
        inv_temperature=100.0,
        return_loss_breakdown=False,
        mask=None,
    ):
        """
        einstein notation
        b - batch
        n - sequence (or flattened spatial dimensions)
        d - feature dimension, which is also log2(codebook size)
        c - number of codebook dim
        """

        x = x.float()

        is_img_or_video = x.ndim >= 4

        # standardize image or video into (batch, seq, dimension)

        if self.channel_first:
            x = rearrange(x, "b d ... -> b ... d")
        if is_img_or_video:
            x, ps = pack([x], "b * d")

        assert (
            x.shape[-1] == self.dim
        ), f"expected dimension of {self.dim} but received {x.shape[-1]}"

        x = self.project_in(x)

        # maybe soft clamp

        if exists(self.soft_clamp_input_value):
            clamp_value = self.soft_clamp_input_value
            x = (x / clamp_value).tanh() * clamp_value

        # split out number of codebooks

        x = rearrange(x, "b n (c d) -> b n c d", c=self.num_codebooks)

        # maybe l2norm

        x = self.maybe_l2norm(x)

        # quantize by eq 3.

        original_input = x

        codebook_value = torch.ones_like(x) * self.codebook_scale
        quantized = torch.where(x > 0, codebook_value, -codebook_value)

        # calculate indices

        indices = reduce(
            (quantized > 0).int() * self.mask.int(), "b n c d -> b n c", "sum"
        )

        # maybe l2norm

        quantized = self.maybe_l2norm(quantized)

        # use straight-through gradients (optionally with custom activation fn) if training

        if self.training:
            x = self.activation(x)
            x = x + (quantized - x).detach()
        else:
            x = quantized

        # entropy aux loss

        if self.training:
            codebook = self.codebook

            codebook = self.maybe_l2norm(codebook)

            # the same as euclidean distance up to a constant
            distance = -2 * einsum("... i d, j d -> ... i j", original_input, codebook)

            prob = (-distance * inv_temperature).softmax(dim=-1)

            # account for mask

            if mask is not None:
                prob = prob[mask]
            else:
                prob = rearrange(prob, "b n ... -> (b n) ...")

            # whether to only use a fraction of probs, for reducing memory

            if self.frac_per_sample_entropy < 1.0:
                num_tokens = prob.shape[0]
                num_sampled_tokens = int(num_tokens * self.frac_per_sample_entropy)
                rand_mask = torch.randn(num_tokens).argsort(dim=-1) < num_sampled_tokens
                per_sample_probs = prob[rand_mask]
            else:
                per_sample_probs = prob

            # calculate per sample entropy

            per_sample_entropy = entropy(per_sample_probs).mean()

            # distribution over all available tokens in the batch

            avg_prob = reduce(per_sample_probs, "... c d -> c d", "mean")

            avg_prob = maybe_distributed_mean(avg_prob)

            codebook_entropy = entropy(avg_prob).mean()

            # 1. entropy will be nudged to be low for each code, to encourage the network to output confident predictions
            # 2. codebook entropy will be nudged to be high, to encourage all codes to be uniformly used within the batch

            entropy_aux_loss = (
                per_sample_entropy - self.diversity_gamma * codebook_entropy
            )
        else:
            # if not training, just return dummy 0
            entropy_aux_loss = per_sample_entropy = codebook_entropy = self.zero

        # whether to make the entropy loss positive or not through a (shifted) softplus

        if self.training and self.experimental_softplus_entropy_loss:
            entropy_aux_loss = F.softplus(entropy_aux_loss + self.entropy_loss_offset)

        # commit loss

        if self.training and self.commitment_loss_weight > 0.0:
            commit_loss = F.mse_loss(
                original_input, quantized.detach(), reduction="none"
            )

            if mask is not None:
                commit_loss = commit_loss[mask]

            commit_loss = commit_loss.mean()
        else:
            commit_loss = self.zero

        # merge back codebook dim

        x = rearrange(x, "b n c d -> b n (c d)")

        # project out to feature dimension if needed

        x = self.project_out(x)

        # reconstitute image or video dimensions

        if is_img_or_video:
            x = unpack_one(x, ps, "b * d")
            indices = unpack_one(indices, ps, "b * c")
        if self.channel_first:
            x = rearrange(x, "b ... d -> b d ...")

        # whether to remove single codebook dim

        if not self.keep_num_codebooks_dim:
            indices = rearrange(indices, "... 1 -> ...")

        # complete aux loss

        aux_loss = (
            entropy_aux_loss * self.entropy_loss_weight
            + commit_loss * self.commitment_loss_weight
        )

        ret = Return(x, indices, aux_loss)

        if not return_loss_breakdown:
            return ret

        return ret, LossBreakdown(per_sample_entropy, codebook_entropy, commit_loss)
