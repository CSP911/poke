---
id: not-linux
name: "Why POKE must not become Linux"
kind: platform-rule
one_liner: "The contract and seven rules that keep the kernel a 64 KB substrate, not an operating system"
injected_by: tools/ontology.py
parent: platform
---
# Why POKE must not become Linux — and the rules that keep it from happening

Status: design principle. Agreed 2026-10-07. Binding for kernel changes.

## The worry

Over two weeks the Pi 4 kernel gained virtual addressing, processes, a
preemptive scheduler, syscalls, a block store and a service channel; SMP
and more processes are next. Mechanism by mechanism, that is the road
Linux walked. The pull toward a general-purpose OS is real.

## Why mechanisms are not the problem

Linux is Linux because of its **contract**: run any program, forever,
compatibly. POSIX, filesystems, users and permissions, packages, a driver
for every device, thousands of resident daemons — thirty million lines
exist to honour that contract, not because MMUs and schedulers are big.

POKE's contract is different and must stay different:

> Run generated, volatile code — one intent's worth — safely, on hardware
> it has been explicitly given.

That contract needs isolation, time slicing, capabilities and a safety
filter. It does not need an API that outlives the code, a userland that
starts itself, or a kernel that knows what a device is. Borrowing Linux's
mechanisms is fine (seL4 borrows the same ones and is not Linux); changing
the contract is what would make POKE Linux.

| | Linux | POKE |
|---|---|---|
| kernel's job | abstract everything | inject, isolate, keep safe, share time — nothing else |
| device code in the kernel | thousands of drivers | zero; every driver is an injected binary (library resident) |
| what runs unasked | daemons, services, a whole userland | nothing; the hub or the device's own store must intend it |
| API | hundreds of POSIX calls, compatible forever | ~10 primitive syscalls; services are data channels, not APIs |
| storage | filesystems, users, permissions | one record log; a memory, not an installation |
| kernel size | 30 MB+ | 37 KB today |

## The rules

1. **Kernel size budget: 64 KB** (`kernel8.img`). At 37 KB now. Crossing
   the budget means something moves out of the kernel, not that the budget
   moves.
2. **Driver boundary.** Device-specific code in the kernel: zero lines.
   The kernel maps windows; residents drive hardware. (See
   `feedback_driver_boundary`, agreed 2026-09-26.)
3. **Nothing resident by default.** No code runs on the device unless the
   hub injected it or the device's store restored it on request. No
   autostart lists, no init system.
4. **Primitive syscalls only.** A syscall is a mechanism (log, exit,
   yield, sleep, mailbox, wait, map). Conveniences live in unit-side
   headers (`ucrt`, `skill_crt.c`, `i2c_bus.h`) that are compiled into the
   volatile binary and die with it.
5. **The regeneration test.** Before adding anything to the kernel ask:
   could a language model regenerate this on demand when an intent needs
   it? If yes, it does not belong in the kernel.
6. **Volatile by default, memory explicit.** What runs is thrown away when
   the intent ends. Remembering (edge store, library) is a separate,
   deliberate act and never implies the code is installed.
7. **No general network stack, no filesystem, no users.** UDP frames for
   the hub protocol, a record log for memory. If something needs TCP, FAT32
   or accounts, it is a resident or it is the hub's job.

## What this does not forbid

- MMU, scheduler, SMP, IPC pages: mechanisms in service of the contract.
- Using Linux elsewhere. The hub runs on Linux/macOS; vision models run on
  GPU boxes. POKE is the body's layer and only there must it not be Linux.
- Growing the library without limit. Residents are many and disposable;
  the kernel is one and small.

## How to apply

A kernel change that adds a feature should name which rule it serves. A
review that finds a convenience API, a device detail, or an autostart in
the kernel should move it to a resident or a unit header. Check the size
in every kernel commit message.
