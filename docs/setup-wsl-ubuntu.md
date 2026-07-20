# WSL Ubuntu Setup

Solana Agent expects Ubuntu on WSL when running on Windows.

## Required State

- WSL enabled
- one Ubuntu distribution installed
- Solana, Rust, Anchor, Node, and Yarn installed inside Ubuntu

## Verify WSL

```powershell
wsl.exe --status
wsl.exe --list --quiet
```

## Install Ubuntu

Preferred path:

```powershell
wsl.exe --list --online
wsl.exe --install Ubuntu
```

If the automatic catalog fetch fails due to network restrictions, install Ubuntu through a trusted package source and then confirm it appears in:

```powershell
wsl.exe --list --quiet
```

## After Ubuntu Exists

Open the Ubuntu shell and install the Solana toolchain there. The runtime then uses WSL automatically.
