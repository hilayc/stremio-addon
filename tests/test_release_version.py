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


@pytest.mark.parametrize('concurrent_version,expected_puts', [('1.2.0', 2), ('1.4.0', 1)])
def test_concurrent_update(monkeypatch, concurrent_version, expected_puts):
    monkeypatch.setenv('GITHUB_TOKEN', 'test')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')
    calls = []
    reads = []
    def api(method, url, token, payload=None):
        if method == 'GET':
            version = '1.1.0' if not reads else concurrent_version
            reads.append(True)
            return {'sha': str(len(reads)), 'content': base64.b64encode(f'version: {version}\nother: preserved\n'.encode()).decode()}
        calls.append(payload)
        if len(calls) == 1:
            raise HTTPError(url, 409, 'Conflict', {}, None)
        assert payload['sha'] == '2'
        assert '[skip ci]' in payload['message']
        assert payload['branch'] == 'main'
        assert base64.b64decode(payload['content']).decode() == 'version: "1.3.0"\nother: preserved\n'
        return {}
    release.update_main('1.3.0', api_request=api, pause=lambda _: None)
    assert len(calls) == expected_puts
