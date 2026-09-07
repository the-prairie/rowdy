"""Native write identities use registered files, not platform-specific spellings."""
import unittest
from urllib.error import HTTPError

import test_native_edits as fixtures
from rowdy import native_edits


class NativePathTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NativeEditTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_parent_alias_keeps_request_identity_and_guarded_apply_undo(self):
        f = self.fixture
        registered = f.path
        alias = f.root.parent / 'alias'
        alias.symlink_to(f.root, target_is_directory=True)
        f.path = alias / 'models/unified_events.sql'
        reviewed = f.review()
        self.assertEqual(reviewed['path'], str(f.path))
        self.assertEqual(registered.read_text(), f.before)
        applied, _ = f.apply(reviewed)
        self.assertEqual(registered.read_text(), f.good)
        review_undo = f.undo_review(applied)
        undone = native_edits.invoke(f.home, f.request('undo', review_undo['receipt']['id'], review_undo['current_binding']))
        self.assertEqual(undone['path'], str(f.path))
        self.assertEqual(undone['receipt']['status'], 'undone')
        self.assertEqual(registered.read_text(), f.before)

    def test_equal_content_in_unregistered_file_is_not_the_same_resource(self):
        f = self.fixture
        reviewed = f.review()
        other = f.root / 'models/unregistered.sql'
        other.write_text(f.before)
        payload = f.request('apply', reviewed['receipt']['id'], reviewed['current_binding'])
        with self.assertRaises((ValueError, HTTPError)):
            native_edits.invoke(f.home, {**payload, 'path': str(other)})
        self.assertEqual(f.path.read_text(), f.before)

    def test_registered_source_replaced_with_symlink_still_denied(self):
        f = self.fixture
        reviewed = f.review()
        other = f.root.parent / 'outside.sql'
        other.write_text(f.before)
        f.path.unlink()
        f.path.symlink_to(other)
        payload = f.request('apply', reviewed['receipt']['id'], reviewed['current_binding'])
        with self.assertRaises((ValueError, HTTPError)):
            native_edits.invoke(f.home, payload)
        self.assertEqual(other.read_text(), f.before)


if __name__ == '__main__':
    unittest.main()
