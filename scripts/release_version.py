"""Validate release tags and advance the HA version without triggering CI."""
import argparse
import base64
import json
import os
import re
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def version_key(version):
    match = re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?', version)
    if not match or len(version) > 128:
        raise ValueError('Use a semantic version such as v1.0.4 or v1.0.4-rc.1; build metadata (+) is not supported by Docker tags.')
    pre = match[4]
    identifiers = []
    for part in pre.split('.') if pre else []:
        if part.isdigit() and len(part) > 1 and part.startswith('0'):
            raise ValueError('Numeric prerelease identifiers cannot have leading zeros')
        identifiers.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(map(int, match.group(1, 2, 3))) + (pre is None, tuple(identifiers))


def release_version(tag):
    if not tag.startswith('v'):
        raise ValueError('Release tags must start with v, for example v1.0.4')
    version = tag[1:]
    version_key(version)
    return version


def update_content(content, version):
    # Change only the top-level version line, preserving unrelated configuration.
    matches = list(re.finditer(r'^version:[^\r\n]*', content, flags=re.M))
    if len(matches) != 1:
        raise ValueError('Expected exactly one top-level version in addon/config.yaml')
    match = matches[0]
    current = match[0].split(':', 1)[1].split('#', 1)[0].strip().strip('\"\'')
    if version_key(current.removeprefix('v')) >= version_key(version):
        return None
    return content[:match.start()] + f'version: "{version}"' + content[match.end():]


def request(method, url, token, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(url, data=data, method=method, headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28',
    })
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def update_main(version, api_request=request, pause=time.sleep):
    token = os.environ['GITHUB_TOKEN']
    endpoint = (os.environ.get('GITHUB_API_URL', 'https://api.github.com')
                + '/repos/' + os.environ['GITHUB_REPOSITORY'] + '/contents/addon/config.yaml')
    for attempt in range(5):
        original = api_request('GET', endpoint + '?ref=main', token)
        content = base64.b64decode(original['content']).decode('utf-8')
        updated = update_content(content, version)
        if updated is None:
            print('Home Assistant already advertises this version or a newer release; no update needed.')
            return
        try:
            api_request('PUT', endpoint, token, {
                'message': f'chore: release Home Assistant add-on {version} [skip ci]',
                'content': base64.b64encode(updated.encode()).decode(),
                'sha': original['sha'], 'branch': 'main',
            })
            print(f'Updated addon/config.yaml on main to {version}.')
            return
        except HTTPError as exc:
            if exc.code != 409 or attempt == 4:
                raise
            # Another release or user edit won the race. Re-read and compare
            # versions again rather than overwriting the latest configuration.
            pause(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'update'])
    args = parser.parse_args()
    version = release_version(os.environ['RELEASE_TAG'])
    if args.command == 'prepare':
        image = f"ghcr.io/{os.environ['GITHUB_REPOSITORY'].lower()}:{version}"
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write(f'version={version}\nname={image}\n')
    else:
        update_main(version)


if __name__ == '__main__':
    main()
