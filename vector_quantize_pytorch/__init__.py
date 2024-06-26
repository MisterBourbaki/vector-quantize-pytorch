from vector_quantize_pytorch.finite_scalar_quantization import FSQ
from vector_quantize_pytorch.latent_quantization import LatentQuantize
from vector_quantize_pytorch.lookup_free_quantization import LFQ
from vector_quantize_pytorch.random_projection_quantizer import (
    RandomProjectionQuantizer,
)
from vector_quantize_pytorch.residual_fsq import GroupedResidualFSQ, ResidualFSQ
from vector_quantize_pytorch.residual_lfq import GroupedResidualLFQ, ResidualLFQ
from vector_quantize_pytorch.residual_vq import GroupedResidualVQ, ResidualVQ
from vector_quantize_pytorch.vector_quantize_pytorch import VectorQuantize

__all__ = [
    "FSQ",
    "LatentQuantize",
    "LFQ",
    "RandomProjectionQuantizer",
    "GroupedResidualFSQ",
    "GroupedResidualLFQ",
    "GroupedResidualVQ",
    "ResidualFSQ",
    "ResidualLFQ",
    "ResidualVQ",
    "VectorQuantize",
]
