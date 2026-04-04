def run_single_from_env(*args, **kwargs):
    from .run_single import run_single_from_env as _impl

    return _impl(*args, **kwargs)


def run_batch_from_env(*args, **kwargs):
    from .run_batch import run_batch_from_env as _impl

    return _impl(*args, **kwargs)

__all__ = ["run_single_from_env", "run_batch_from_env"]
