"""Expose the image's release version as a valid Stremio manifest version."""
import os
import re


def get_version():
    version = os.environ.get('APP_VERSION', '').strip().removeprefix('v')
    match = re.fullmatch(
        r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
        r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
        r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?', version,
    )
    if match:
        pre = match[4]
        if not pre or all(not (part.isdigit() and len(part) > 1 and part.startswith('0'))
                          for part in pre.split('.')):
            return version
    # Local runs and untagged builds use BUILD_VERSION=main by default.
    return '0.0.0-dev'
