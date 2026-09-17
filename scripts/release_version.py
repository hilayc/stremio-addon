"""Synchronize the HA version and release notes without triggering CI."""
import argparse
import base64
import json
import os
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote
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


def ensure_release(version, api_request=request):
    """Generate notes only for a new release; preserve existing release bodies."""
    token = os.environ['GITHUB_TOKEN']
    endpoint = (os.environ.get('GITHUB_API_URL', 'https://api.github.com')
                + '/repos/' + os.environ['GITHUB_REPOSITORY'])
    tag = 'v' + version
    try:
        api_request('GET', endpoint + '/releases/tags/' + quote(tag, safe=''), token)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        api_request('POST', endpoint + '/releases', token, {
            'tag_name': tag, 'name': tag, 'generate_release_notes': True,
        })


def update_changelog(content, version, notes):
    # Older workflow reruns appended the generated comparison link twice.
    # Repair only that exact generated paragraph, preserving other release text.
    seen_links = set()
    paragraphs = []
    for paragraph in notes.strip().split('\n\n'):
        if re.fullmatch(r'\*\*Full Changelog\*\*: https://github\.com/\S+/compare/\S+', paragraph):
            if paragraph in seen_links:
                continue
            seen_links.add(paragraph)
        paragraphs.append(paragraph)
    notes = '\n\n'.join(paragraphs)
    start = f'<!-- release:{version}:start -->'
    end = f'<!-- release:{version}:end -->'
    entry = f'{start}\n## {version}\n\n{notes.strip()}\n{end}'
    pattern = re.compile(re.escape(start) + r'.*?' + re.escape(end), re.S)
    if pattern.search(content):
        return pattern.sub(lambda _: entry, content, count=1)
    if not content.strip():
        return '# Changelog\n\n' + entry + '\n'
    title = re.match(r'\A# [^\n]+\n(?:\s*\n)*', content)
    if title:
        return content[:title.end()] + entry + '\n\n' + content[title.end():]
    return entry + '\n\n' + content


def update_main(version, api_request=request, pause=time.sleep):
    token = os.environ['GITHUB_TOKEN']
    endpoint = (os.environ.get('GITHUB_API_URL', 'https://api.github.com')
                + '/repos/' + os.environ['GITHUB_REPOSITORY'])
    tag = 'v' + version
    release = api_request('GET', endpoint + '/releases/tags/' + quote(tag, safe=''), token)
    if release.get('draft') or release.get('tag_name') != tag:
        raise ValueError('Expected a published GitHub Release for the requested tag')
    notes = release.get('body') or ''

    def read_file(path, head, optional=False):
        try:
            result = api_request('GET', endpoint + '/contents/' + path + '?ref=' + head, token)
            return base64.b64decode(result['content']).decode('utf-8')
        except HTTPError as exc:
            if optional and exc.code == 404:
                return ''
            raise

    for attempt in range(5):
        head = api_request('GET', endpoint + '/git/ref/heads/main', token)['object']['sha']
        commit = api_request('GET', endpoint + '/git/commits/' + head, token)
        content = read_file('addon/config.yaml', head)
        updated = update_content(content, version)
        # Equal versions may still need the changelog (for example on a rerun).
        if updated is None:
            match = re.search(r'^version:([^\r\n]*)', content, re.M)
            current = match[1].split('#', 1)[0].strip().strip('\"\'').removeprefix('v')
            if version_key(current) > version_key(version):
                print('Home Assistant already advertises a newer release; skipping older release.')
                return
        changelog = read_file('addon/CHANGELOG.md', head, optional=True)
        new_changelog = update_changelog(changelog, version, notes)
        entries = []
        if updated is not None:
            entries.append({'path': 'addon/config.yaml', 'mode': '100644', 'type': 'blob', 'content': updated})
        if new_changelog != changelog:
            entries.append({'path': 'addon/CHANGELOG.md', 'mode': '100644', 'type': 'blob', 'content': new_changelog})
        if not entries:
            print('Home Assistant version and release notes are already up to date.')
            return
        tree = api_request('POST', endpoint + '/git/trees', token,
                           {'base_tree': commit['tree']['sha'], 'tree': entries})
        new_commit = api_request('POST', endpoint + '/git/commits', token, {
            'message': f'chore: release Home Assistant add-on {version} [skip ci]',
            'tree': tree['sha'], 'parents': [head],
        })
        try:
            api_request('PATCH', endpoint + '/git/refs/heads/main', token, {
                'sha': new_commit['sha'], 'force': False,
            })
            print(f'Updated Home Assistant version and changelog on main for {version}.')
            return
        except HTTPError as exc:
            if exc.code not in (409, 422) or attempt == 4:
                raise
            latest = api_request('GET', endpoint + '/git/ref/heads/main', token)['object']['sha']
            if latest == head:
                # Validation/protection failures are not concurrency conflicts.
                raise
            # Another release or user edit won the race. Re-read and compare
            # versions again rather than overwriting the latest configuration.
            pause(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'release', 'update'])
    args = parser.parse_args()
    version = release_version(os.environ['RELEASE_TAG'])
    if args.command == 'prepare':
        image = f"ghcr.io/{os.environ['GITHUB_REPOSITORY'].lower()}:{version}"
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write(f'version={version}\nname={image}\n')
    elif args.command == 'release':
        ensure_release(version)
    else:
        update_main(version)


if __name__ == '__main__':
    main()
