import torch

from vector_quantize_pytorch import VectorQuantize


class TestVectorQuantizer:
    quantizer = VectorQuantize(
        dim=4,
        codebook_size=2**5,  # codebook size
        decay=0.8,  # the exponential moving average decay, lower means the dictionary will change faster
        commitment_weight=1.0,  # the weight on the commitment loss
    )

    def test_init(self):
        assert self.quantizer

    def test_forward(self, vectors_channel_last):
        # features = torch.randn(1, 1024, 4)  # 4 since there are 4 levels
        x = torch.randn(1, 1024, 4)
        quantized, indices, _ = self.quantizer(x)

        assert x.shape == quantized.shape
        assert indices.shape == (1, 1024)

        # for features in vectors_channel_last:
        #     quantized, indices, _ = self.quantizer(features)

        #     assert quantized.shape == features.shape
        #     # assert torch.all(quantized == self.quantizer.indices_to_codes(indices))
        #     assert indices.shape == features.shape[:-1]


class TestVectorQuantizerCosine:
    quantizer = VectorQuantize(
        # dim=256,
        dim=4,
        codebook_size=2**5,  # codebook size
        use_cosine_sim=True,
    )

    def test_init(self):
        assert self.quantizer

    def test_forward(self):
        # x = torch.randn(1, 1024, 256)
        x = torch.randn(1, 1024, 4)
        quantized, indices, _ = self.quantizer(x)

        assert x.shape == quantized.shape
        assert indices.shape == (1, 1024)


class TestVectorQuantizerMultihead:
    quantizer = VectorQuantize(
        dim=4,
        codebook_dim=32,  # a number of papers have shown smaller codebook dimension to be acceptable
        heads=8,  # number of heads to vector quantize, codebook shared across all heads
        separate_codebook_per_head=True,  # whether to have a separate codebook per head. False would mean 1 shared codebook
        codebook_size=8196,
        accept_image_fmap=True,
    )

    def test_init(self):
        assert self.quantizer

    def test_forward(self):
        img_fmap = torch.randn(1, 4, 32, 32)
        quantized, indices, _ = self.quantizer(img_fmap)

        assert img_fmap.shape == quantized.shape
        assert indices.shape == (1, 32, 32, 8)


class TestVectorQuantizerLowerCode:
    quantizer = VectorQuantize(
        dim=4,
        codebook_size=256,
        codebook_dim=2,  # paper proposes setting this to 32 or as low as 8 to increase codebook usage
    )

    def test_init(self):
        assert self.quantizer

    def test_forward(self):
        x = torch.randn(1, 1024, 4)
        quantized, indices, _ = self.quantizer(x)

        assert x.shape == quantized.shape
        assert indices.shape == (1, 1024)


class TestVectorQuantizerNoInit:
    quantizer = VectorQuantize(
        dim=4,
        codebook_size=512,
        decay=0.8,
        commitment_weight=1.0,
        kmeans_init=True,
    )

    def test_init(self):
        assert self.quantizer

    def test_forward(self):
        x = torch.randn(1, 1024, 4)
        quantized, indices, _ = self.quantizer(x)

        assert x.shape == quantized.shape
        assert indices.shape == (1, 1024)