---
id: devices
name: "Devices and identification"
kind: topic
one_liner: "What is known about particular chips: how to recognise one on a bus and how to read it"
injected_by: tools/ontology.py
role: representative
use_when: "an unknown I2C address answered and you want to know what it probably is · how to read a temperature / time / magnetic field from a known part · ID registers and value formats (TMP10x, DS1307/1338, HMC5883/LSM303, PCA9552, eGalax touch, VL805 hub) · which QEMU device models misbehave"
---
# Devices and identification
Knowledge about chips, separate from the drivers that use them: identification registers, value
formats, quirks of the QEMU models, and the recipes an incubation produced. When something unknown
answers on a bus, this is where to look before probing blind.
