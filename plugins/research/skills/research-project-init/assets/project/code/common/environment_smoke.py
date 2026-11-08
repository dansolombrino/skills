"""Bounded GPU smoke: prove the locked runtime can actually compute on a device."""

import sys


def main() -> int:
    import torch

    if not torch.cuda.is_available():
        print("gpu smoke FAIL: no CUDA device visible", file=sys.stderr)
        return 1

    device = torch.device("cuda:0")
    name = torch.cuda.get_device_name(device)
    a = torch.randn(256, 256, device=device)
    b = torch.randn(256, 256, device=device)
    result = (a @ b).sum().item()
    torch.cuda.synchronize()

    if result != result:  # NaN guard
        print(f"gpu smoke FAIL: NaN result on {name}", file=sys.stderr)
        return 1

    print(f"gpu smoke OK: {name}, torch {torch.__version__}, cuda {torch.version.cuda}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
