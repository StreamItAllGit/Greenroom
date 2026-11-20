# @module greenroom.companion.__init__
# @purpose Companion package entrypoint re-exporting create_companion_app.
# @inputs Runtime ASGI app arguments (db, config, secret_path, config_file_path)
# @outputs create_companion_app
"""Greenroom Companion: Always-on Windows desktop & mobile PWA interface."""


def create_companion_app(*args, **kwargs):
    from greenroom.companion.server import create_companion_app as _create

    return _create(*args, **kwargs)


__all__ = ["create_companion_app"]
