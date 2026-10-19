import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_catalog import imdb_id, eligible_release_threads, source_eligible, process

class CatalogTests(unittest.TestCase):
    def test_imdb_zero_padding(self):
        self.assertEqual(imdb_id(1), 'tt0000001')
        self.assertEqual(imdb_id(12345678), 'tt12345678')
        self.assertIsNone(imdb_id('invalid'))
    def test_resolution_and_audio(self):
        row = {'threads': [{'id': 11, 'title': 'Film 720p Ukr/Eng'}, {'id': 12, 'title': 'Film WEB-DL 1080p Ukr/Eng'},
                           {'id': 13, 'title': 'Film 4K 2160p Eng | Sub Ukr'}, {'id': 14, 'title': 'Film 2160p HDR 2xUkr'}]}
        self.assertEqual(eligible_release_threads(row), {12, 14})
    def test_genre_exclusions(self):
        row = {'imdb_id': 'tt1234567', 'title': 'Film', 'genres': [{'name':'документальний'}], 'threads': [{'id': 1, 'title':'Film 1080p Ukr'}]}
        self.assertFalse(source_eligible(row))
    def test_dedup_and_updates(self):
        import json
        rows = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/sample_source.json').read_text())['data']
        catalog, state, report = process(rows, {'movies': {}, 'tmdb_cache': {}}, '', 60, demo=True)
        self.assertEqual(len(catalog), 2)
        self.assertEqual([x['id'] for x in catalog], [101, 102])
        self.assertEqual(state['movies']['tt0000001']['thread_ids'], [1001,1002])
        self.assertEqual(report['added'], 2)
        catalog2, state2, report2 = process(rows, state, '', 60, demo=True)
        self.assertEqual(report2['added'], 0)
        self.assertEqual(report2['updated'], 0)
        rows[0]['threads'].append({'id': 1022, 'title': 'Film BDRemux 1080p Ukr/Eng'})
        _, _, report3 = process(rows, state2, '', 60, demo=True)
        self.assertEqual(report3['updated'], 1)

if __name__ == '__main__':
    unittest.main()
