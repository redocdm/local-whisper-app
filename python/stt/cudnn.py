import os
import sys

from util.logging_utils import log


def setup_cudnn_dll_path() -> None:
    """Add cuDNN DLL directory to Windows DLL search path before model initialization."""
    if sys.platform != "win32":
        return

    # IMPORTANT: keep add_dll_directory handles alive for the life of the process.
    # If the returned handle is GC'd, Windows removes that directory from the search path.
    global _DLL_DIR_HANDLES  # noqa: PLW0603
    try:
        _DLL_DIR_HANDLES
    except NameError:
        _DLL_DIR_HANDLES = []  # type: ignore[assignment]

    vendor_cuda11_dir = os.path.join(os.path.dirname(__file__), "..", "vendor", "nvidia", "cuda11")
    vendor_cuda11_dir = os.path.abspath(vendor_cuda11_dir)

    cudnn_paths = [
        vendor_cuda11_dir,
        os.getenv("CUDNN_PATH", ""),
        os.path.join(os.getenv("CUDA_PATH", ""), "bin"),
        os.path.join(os.getenv("CUDA_PATH", ""), "lib"),
        r"C:\Program Files\Blackmagic Design\DaVinci Resolve",
    ]

    try:
        import site

        for site_pkg in site.getsitepackages() + [site.getusersitepackages()]:
            nvidia_cudnn_path = os.path.join(site_pkg, "nvidia", "cudnn", "bin")
            if os.path.exists(nvidia_cudnn_path):
                cudnn_paths.insert(0, nvidia_cudnn_path)
    except Exception:
        pass

    for p in cudnn_paths:
        if not p or not os.path.exists(p):
            continue

        marker_dlls = [
            "cudnn_ops64_9.dll",
            "cudnn_ops_infer64_8.dll",
            "cudnn_cnn_infer64_8.dll",
            "cublas64_11.dll",
        ]
        if any(os.path.exists(os.path.join(p, dll)) for dll in marker_dlls):
            try:
                _DLL_DIR_HANDLES.append(os.add_dll_directory(p))  # type: ignore[attr-defined]
                log(f"Added cuDNN DLL directory: {p}")
                return
            except Exception as e:
                log(f"Failed to add DLL directory {p}: {e}")

    log("Warning: cuDNN DLL not found. GPU operations may fail. Install cuDNN or set CUDNN_PATH.")


