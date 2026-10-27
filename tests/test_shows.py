import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_shows import season_episodes, thread_data, source_candidate, prepare

class ShowTests(unittest.TestCase):
    def test_season_and_episode_range(self):
        self.assertEqual(season_episodes('Game of Thrones (Сезон 3, 01-07 серій) WEB-DL'), {3: set(range(1, 8))})
        self.assertEqual(season_episodes('Show S02E03-E05 1080p'), {2: {3,4,5}})
        self.assertEqual(season_episodes('Show (Season 1) WEB-DL'), {1: None})
        self.assertEqual(season_episodes('Show (Сезон 2, 01-06) WEB-DL'), {2: set(range(1,7))})
        self.assertEqual(season_episodes('Show S02 1080p'), {2: None})

    def test_filter_subtitles_only(self):
        row = {'threads':[{'id':45, 'title':'Show (Сезон 1) WEB-DL 1080p Eng | Sub Ukr'}]}
        self.assertEqual(thread_data(row)[0], set())

    def test_dedupe_release_versions(self):
        row = {'imdb_id':'tt0944947','title':'Game of Thrones','imdb_rating':9.2,'imdb_votes':2000000,
               'genres':[{'name':'драма'}],'threads':[
                   {'id':10,'title':'Гра престолів (Сезон 3, 01-05 серій) WEB-DL 1080p Ukr/Eng'},
                   {'id':11,'title':'Гра престолів (Сезон 3, 01-10 серій) 4K Ukr/Eng'}]}
        self.assertTrue(source_candidate(row))
        self.assertEqual(thread_data(row)[1], {3: set(range(1,11))})

    def test_existing_thread_new_episodes_triggers_update(self):
        row={'imdb_id':'tt0944947','title':'Game of Thrones','title_uk':'Гра престолів', 'year':2011,
             'imdb_rating':9.2,'imdb_votes':2000000,'genres':[{'name':'драма'}],
             'threads':[{'id':1, 'title':'Гра престолів (Сезон 3, 01-04 серій) 1080p Ukr/Eng'}]}
        catalog, state, report=prepare([row],{},'',60,7.5,20000,True)
        self.assertEqual(report['new'],1)
        row['threads'][0]['title']='Гра престолів (Сезон 3, 01-08 серій) 1080p Ukr/Eng'
        newer, saved, report=prepare([row],state,'',60,7.5,20000,True)
        self.assertEqual(report['updated'],1)
        self.assertEqual(len(newer[0]['seasons'][0]['episodes']),8)

    def test_exclude_anime(self):
        row = {'imdb_id':'tt0944947','imdb_rating':9.0,'imdb_votes':900000,
               'genres':[{'name':'анімація'}], 'threads':[{'id':1,'title':'Show (Сезон 1) WEB-DL 1080p Ukr/Eng'}]}
        self.assertFalse(source_candidate(row))

    def test_demo_catalog(self):
        row={'imdb_id':'tt0944947','title':'Game of Thrones','title_uk':'Гра престолів', 'year':2011,
             'imdb_rating':9.2,'imdb_votes':2000000,'genres':[{'name':'драма'}],
             'threads':[{'id':1, 'title':'Гра престолів (Сезон 3, 01-10 серій) 1080p Ukr/Eng'}]}
        catalog, state, report=prepare([row],{},'',60,7.5,20000,True)
        self.assertEqual(len(catalog),1)
        self.assertEqual(catalog[0]['id'],1399)
        self.assertEqual(len(catalog[0]['seasons'][0]['episodes']),10)
        self.assertEqual(len(state['shows']),1)
        self.assertEqual(report['catalog_series'],1)

if __name__=='__main__': unittest.main()
