"""Shared official resource URLs, verified against CCIFJ's published sources.

Membership form and 2026 fees checked 2026-11-04. This is provenance, not a
claim that stored prices will remain current indefinitely.
"""
MEMBERSHIP_PAGE = 'https://www.ccifj.or.jp/en/membership/how-to-become-a-member.html'
MEMBERSHIP_FEE_SOURCE = 'https://www.ccifj.or.jp/en/publications/news/news-detail/news/cci-france-japon-general-meeting-2026-a-look-back-at-the-event.html'
TEAM_PAGE = 'https://www.ccifj.or.jp/en/our-chamber/our-team.html'
MEMBERS_DIRECTORY_PAGE = 'https://www.ccifj.or.jp/en/membership/members-directory.html'
BUSINESS_INQUIRY = 'https://www.ccifj.or.jp/en/commercial-support/contact-us.html'
CV_REGISTRATION = 'https://ccifj-emploi.mycv.tech/index.php?langue=en&module=candidat&action=infosGeneralesCandidatVide&typepage=formulaire&idpage=a94966106b492'


def membership_form(locale='en'):
    language = 'jp' if locale in {'ja', 'jp'} else locale if locale in {'en', 'fr'} else 'en'
    return f'https://survey.ccifi.net/232729?lang={language}'
