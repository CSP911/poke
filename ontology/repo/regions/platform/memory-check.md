---
id: memory-check
name: "Hardware self-check lessons"
kind: lesson
one_liner: "What it took to test 4 GB of RAM at speed: caches on, SMPEN, code in cacheable memory, DMA regions excluded"
injected_by: tools/ontology.py
parent: platform
---
# Hardware self-check lessons
`poke check memory` runs a parameter-patched probe over 64 MB chunks. It was 38 MB/s until three
things were fixed: the MMU had to be on with caches; CPUECTLR_EL1.SMPEN had to be set or the caches
stay off on an A72; and the probe's own code had to live in cacheable memory, because
instruction fetch from a non-cacheable region was the bottleneck (28× once moved). Everything DMA
touches — kernel 0–16 MB, GENET buffers, xHCI DMA, framebuffer — must be excluded and mapped
non-cacheable, or the network dies silently (CPU reads stale cache while the chip wrote DRAM).
`poke check edac` reads CPUMERRSR_EL1 / L2MERRSR_EL1 (A72 cache ECC); the Pi 4 DRAM has no ECC.
