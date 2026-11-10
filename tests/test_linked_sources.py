"""Knowledge sources linked by URL (Calameo, web pages, PDFs): parsing, safety and sync (network mocked)."""
import socket
import unittest
from unittest.mock import patch

import test_api_contract as contract
from concierge import linked_sources as ls
from test_admin_content import AdminContentApiTest

CALAMEO_PAGE = """<html><head><meta property="og:title" content="France Japon Eco 177"></head><body>
<script type="text/javascript">var Calameo = {Data: {"account":{"id":1},"book":{"id":2,"code":"004601149209d7d6bd966",
"name":"France Japon Eco 177","image":{"large":"\\/\\/i.calameoassets.com\\/240307023538-3dcf\\/large.jpg"},
"document":{"pages":84}}}};</script>
<p class="about-truncate readmore">FJE 177 : Innovation...</p>
<p class="about hidden">FJE 177 : Innovation, innovation<br>L&#39;Or&eacute;al growth in Japan and the <b>Business Summit</b>.</p>
</body></html>"""
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]


def page(asset='240307023538-3dcf', summary='Business Summit'):
    return CALAMEO_PAGE.replace('240307023538-3dcf', asset).replace('Business Summit', summary).encode()


class ParsingTest(unittest.TestCase):
    def test_calameo_code_accepts_read_and_books_links_only(self):
        self.assertEqual(ls.calameo_code('https://www.calameo.com/read/004601149209d7d6bd966'), '004601149209d7d6bd966')
        self.assertEqual(ls.calameo_code('https://en.calameo.com/books/004601149209d7d6bd966/'), '004601149209d7d6bd966')
        self.assertIsNone(ls.calameo_code('https://www.calameo.com/accounts/4601149'))
        self.assertIsNone(ls.calameo_code('https://evilcalameo.com/read/004601149209d7d6bd966'))

    def test_signature_matches_calameo_documentation_example(self):
        params = {'apikey': '84c92f2c5686d350d3d2d15c2073ec23', 'action': 'API.fetchAccountBooks', 'output': 'JSON'}
        self.assertEqual(ls.calameo_signature('1jHj0HORiWhpmw9QqOXsLoZjweVm7BWoT5WPWC2AbFsuWl95', params),
                         'C867521B01925CE79E16FD630F2BE646'.lower())

    def test_overview_page_gives_name_pages_asset_and_full_summary(self):
        meta = ls.parse_calameo_page(CALAMEO_PAGE)
        self.assertEqual((meta['name'], meta['pages'], meta['asset']), ('France Japon Eco 177', 84, '240307023538-3dcf'))
        self.assertEqual(meta['summary'], "FJE 177 : Innovation, innovation\nL'Oréal growth in Japan and the Business Summit.")

    def test_only_public_https_hosts_are_fetched(self):
        with self.assertRaises(ls.LinkError):
            ls._check_public_https('http://example.com/a.pdf')
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with patch('socket.getaddrinfo', return_value=private), self.assertRaises(ls.LinkError):
            ls._check_public_https('https://intranet.example/a.pdf')
        with patch('socket.getaddrinfo', return_value=PUBLIC):
            self.assertEqual(ls._check_public_https('https://example.com/a.pdf'), 'https://example.com/a.pdf')

    @patch('socket.getaddrinfo', return_value=PUBLIC)
    def test_calameo_without_download_link_indexes_the_summary(self, _):
        with patch.object(ls, 'http_get', return_value=(page(), 'text/html', 'https://www.calameo.com/books/x')) as get:
            fetched = ls.fetch_source('https://www.calameo.com/read/004601149209d7d6bd966', max_pages=10, max_chars=10_000)
        get.assert_called_once_with('https://www.calameo.com/books/004601149209d7d6bd966')
        self.assertIn('Business Summit', fetched.text)
        self.assertIn('(84 pages)', fetched.text)
        self.assertTrue(fetched.note.startswith('Summary only'))
        self.assertTrue(fetched.version.startswith('calameo:240307023538-3dcf|'))

    @patch('socket.getaddrinfo', return_value=PUBLIC)
    def test_calameo_with_download_link_indexes_the_file(self, _):
        responses = {'https://www.calameo.com/books/004601149209d7d6bd966': (page(), 'text/html'),
                     'https://ccifj.example/fje177.txt': (b'Full article text about semiconductors.', 'text/plain')}
        with patch.object(ls, 'http_get', side_effect=lambda url, **_: (*responses[url], url)):
            fetched = ls.fetch_source('https://www.calameo.com/read/004601149209d7d6bd966',
                                      'https://ccifj.example/fje177.txt', max_pages=10, max_chars=10_000)
        self.assertIn('semiconductors', fetched.text)
        self.assertEqual(fetched.note, '')
        self.assertEqual(fetched.title, 'France Japon Eco 177')


class LinkedKnowledgeApiTest(unittest.TestCase):
    staff_headers = AdminContentApiTest.staff_headers
    tearDown = contract.ApiContractTest.tearDown

    def setUp(self):
        contract.ApiContractTest.setUp(self)
        dns = patch('socket.getaddrinfo', return_value=PUBLIC)
        dns.start()
        self.addCleanup(dns.stop)
        self.pages = {'https://www.calameo.com/books/004601149209d7d6bd966': page()}

    def fake_get(self, url, **_):
        if url not in self.pages:
            raise ls.LinkError('The link answered HTTP 404. Is it public?')
        return self.pages[url], 'text/html', url

    def test_link_sync_and_change_detection(self):
        headers = self.staff_headers()
        url = 'https://www.calameo.com/read/004601149209d7d6bd966'
        with patch.object(ls, 'http_get', side_effect=self.fake_get), \
                patch('concierge.knowledge.index_document', return_value=3) as index, \
                patch('concierge.knowledge.remove_document'):
            created = self.client.post('/api/v1/admin/knowledge/link', json={'url': url}, headers=headers)
            self.assertEqual(created.status_code, 201, created.text)
            doc = created.json()
            self.assertEqual((doc['title'], doc['linked'], doc['chunk_count']), ('France Japon Eco 177', True, 3))
            self.assertTrue(doc['sync_note'].startswith('Summary only'))
            self.assertEqual(index.call_args.args[2], url)  # cited with the Calameo link
            self.assertEqual(self.client.post('/api/v1/admin/knowledge/link', json={'url': url}, headers=headers).status_code, 409)

            sync = f"/api/v1/admin/knowledge/{doc['doc_id']}/sync"
            self.assertFalse(self.client.post(sync, headers=headers).json()['changed'])
            self.assertEqual(index.call_count, 1)

            # Staff replace the publication on Calameo: new cover asset and summary.
            self.pages['https://www.calameo.com/books/004601149209d7d6bd966'] = page('250101000000-beef', 'Tax reform 2025')
            result = self.client.post(sync, headers=headers).json()
            self.assertTrue(result['changed'])
            self.assertIn('Tax reform 2025', index.call_args.args[3])

            # Calameo is down: the indexed version stays and the error is shown.
            self.pages.clear()
            result = self.client.post(sync, headers=headers).json()
            self.assertFalse(result['changed'])
            self.assertTrue(result['sync_note'].startswith('Last check failed'))
            self.assertEqual(result['chunk_count'], 3)

    def test_bad_links_are_refused(self):
        headers = self.staff_headers()
        with patch.object(ls, 'http_get', side_effect=self.fake_get):
            self.assertEqual(self.client.post('/api/v1/admin/knowledge/link', json={'url': 'http://insecure.example/x'},
                                              headers=headers).status_code, 422)
            missing = self.client.post('/api/v1/admin/knowledge/link', json={'url': 'https://ccifj.example/gone.pdf'}, headers=headers)
            self.assertEqual(missing.status_code, 422)
            self.assertIn('404', missing.json()['detail'])


if __name__ == '__main__':
    unittest.main()
