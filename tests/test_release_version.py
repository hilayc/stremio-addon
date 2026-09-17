import base64
import importlib.util
from pathlib import Path
from urllib.error import HTTPError

import pytest

spec = importlib.util.spec_from_file_location('release_version', Path(__file__).parents[1] / 'scripts/release_version.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.mark.parametrize('tag,expected', [('v1.0.4', '1.0.4'), ('v1.2.0-rc.1', '1.2.0-rc.1')])
def test_strip_prefix(tag, expected):
    assert release.release_version(tag) == expected


@pytest.mark.parametrize('tag', ['1.0.4', 'vv1.0.4', 'v', 'v01.2.3', 'v1.2.3+build', 'v1.2.3-01', 'v../../main'])
def test_bad_tag(tag):
    with pytest.raises(ValueError):
        release.release_version(tag)


def test_preserve_config_and_prevent_rollback():
    text = 'name: Test\nversion: "1.9.0"\noptions:\n  value: 123\n'
    assert release.update_content(text, '1.10.0') == text.replace('1.9.0', '1.10.0')
    assert release.update_content(text, '1.9.0') is None
    assert release.update_content(text, '1.8.9') is None
    assert release.update_content(text, '1.9.0-rc.1') is None
    assert release.version_key('1.9.0-rc.10') > release.version_key('1.9.0-rc.2')


class GitHub:
    def __init__(self, version='1.1.0', changelog=None, conflict=None):
        self.head = 'original'
        self.version = version
        self.changelog = changelog
        self.conflict = conflict
        self.trees = []
        self.commits = []
        self.updates = []

    def __call__(self, method, url, token, payload=None):
        if '/releases/tags/' in url:
            return {'tag_name': 'v1.3.0', 'draft': False, 'body': '## Changes\n- Hebrew search improved.\n'}
        if method == 'GET' and url.endswith('/git/ref/heads/main'):
            return {'object': {'sha': self.head}}
        if method == 'GET' and '/git/commits/' in url:
            return {'tree': {'sha': 'tree-' + self.head}}
        if method == 'GET' and '/contents/' in url:
            assert url.endswith('?ref=' + self.head)
            if '/config.yaml?' in url:
                text = f'version: {self.version}\nother: preserved\n'
            else:
                text = self.changelog
                if text is None:
                    raise HTTPError(url, 404, 'Not Found', {}, None)
            return {'content': base64.b64encode(text.encode()).decode()}
        if method == 'POST' and url.endswith('/git/trees'):
            assert payload['base_tree'] == 'tree-' + self.head
            self.trees.append(payload['tree'])
            return {'sha': 'new-tree'}
        if method == 'POST' and url.endswith('/git/commits'):
            assert payload['parents'] == [self.head]
            assert '[skip ci]' in payload['message']
            self.commits.append(payload)
            return {'sha': 'new-commit'}
        if method == 'PATCH' and url.endswith('/git/refs/heads/main'):
            assert payload == {'sha': 'new-commit', 'force': False}
            self.updates.append(payload)
            if self.conflict:
                self.version = self.conflict
                self.conflict = None
                self.head = 'concurrent'
                raise HTTPError(url, 422, 'Not fast forward', {}, None)
            return {}
        raise AssertionError((method, url))


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'test')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')


def test_atomic_config_and_changelog(auth):
    api = GitHub()
    release.update_main('1.3.0', api_request=api)
    files = {e['path']: e['content'] for e in api.trees[0]}
    assert files['addon/config.yaml'] == 'version: "1.3.0"\nother: preserved\n'
    assert '## 1.3.0\n\n## Changes\n- Hebrew search improved.' in files['addon/CHANGELOG.md']
    assert len(api.commits) == len(api.updates) == 1


@pytest.mark.parametrize('concurrent_version,expected_updates', [('1.2.0', 2), ('1.4.0', 1)])
def test_concurrent_update(auth, concurrent_version, expected_updates):
    api = GitHub(conflict=concurrent_version)
    release.update_main('1.3.0', api_request=api, pause=lambda _: None)
    assert len(api.updates) == expected_updates


def test_changelog_preserves_history_and_reruns():
    old = '# Changelog\n\n## 1.2.0\n\n- Older notes.\n'
    updated = release.update_changelog(old, '1.3.0', '## Changes\n- New notes.')
    assert updated.endswith('## 1.2.0\n\n- Older notes.\n')
    assert updated.index('## 1.3.0') < updated.index('## 1.2.0')
    assert release.update_changelog(updated, '1.3.0', '## Changes\n- New notes.') == updated
    edited = release.update_changelog(updated, '1.3.0', '- Edited notes.')
    assert edited.count('## 1.3.0') == 1
    assert '- New notes.' not in edited
    assert '- Edited notes.' in edited


def test_same_version_missing_changelog_is_repaired(auth):
    api = GitHub(version='1.3.0')
    release.update_main('1.3.0', api_request=api)
    assert [e['path'] for e in api.trees[0]] == ['addon/CHANGELOG.md']


def test_up_to_date_is_noop(auth):
    changelog = release.update_changelog('', '1.3.0', '## Changes\n- Hebrew search improved.\n')
    api = GitHub(version='1.3.0', changelog=changelog)
    release.update_main('1.3.0', api_request=api)
    assert not api.trees


def test_permission_failure_does_not_retry(auth):
    api = GitHub()
    def denied(method, url, token, payload=None):
        if method == 'PATCH':
            raise HTTPError(url, 403, 'Forbidden', {}, None)
        return api(method, url, token, payload)
    with pytest.raises(HTTPError) as exc:
        release.update_main('1.3.0', api_request=denied)
    assert exc.value.code == 403
    assert len(api.commits) == 1


def test_existing_release_notes_are_never_regenerated(auth):
    calls = []
    def api(method, url, token, payload=None):
        calls.append(method)
        return {'tag_name': 'v1.3.0', 'body': 'Existing notes'}
    release.ensure_release('1.3.0', api_request=api)
    release.ensure_release('1.3.0', api_request=api)
    assert calls == ['GET', 'GET']


def test_duplicate_generated_comparison_link_is_repaired():
    link = '**Full Changelog**: https://github.com/owner/repo/compare/v1.2.0...v1.3.0'
    notes = '## Changes\n- Keep this.\n\n' + link + '\n\n' + link
    updated = release.update_changelog('', '1.3.0', notes)
    assert updated.count(link) == 1
    assert '## Changes\n- Keep this.' in updated
    assert release.update_changelog(updated, '1.3.0', notes) == updated


def test_missing_release_generates_notes_once(auth):
    calls = []
    def api(method, url, token, payload=None):
        calls.append((method, payload))
        if method == 'GET':
            raise HTTPError(url, 404, 'Not Found', {}, None)
        assert url.endswith('/releases')
        return {}
    release.ensure_release('1.3.0', api_request=api)
    assert calls == [('GET', None), ('POST', {
        'tag_name': 'v1.3.0', 'name': 'v1.3.0', 'generate_release_notes': True,
    })]


def test_release_lookup_failure_does_not_create_release(auth):
    def api(method, url, token, payload=None):
        assert method == 'GET'
        raise HTTPError(url, 403, 'Forbidden', {}, None)
    with pytest.raises(HTTPError):
        release.ensure_release('1.3.0', api_request=api)
