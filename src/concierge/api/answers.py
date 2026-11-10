"""Small deterministic answers for common, read-only concierge questions.

Prices and categories come from SQL on each request, never a cached FAQ or RAG
snapshot. These paths need no model initialization and perform no writes.
"""
from concierge.db.relationaldb import crud
from concierge.db.relationaldb.datetimes import japan_now
from concierge.resources import MEMBERSHIP_FEE_SOURCE, membership_form
from .catalog import canonical_question, localized_question
from .events import event_card


async def _upcoming_events(locale, session):
    """The next few events still open for registration, as event cards."""
    events = list(await crud.search_events(session, date_from=japan_now(), limit=30))
    heading = {"en": "Upcoming CCIFJ events", "fr": "Événements CCIFJ à venir", "ja": "今後のCCIFJイベント"}[locale]
    note = {"en": "Choose an event to see its details. Times are shown in Japan time (JST).", "fr": "Choisissez un événement pour en savoir plus. Les horaires sont indiqués à l’heure du Japon (JST).", "ja": "イベントを選択して詳細をご確認ください。日時は日本時間（JST）です。"}[locale]
    empty = {"en": "No upcoming events are listed. Ask me to help you contact the events team.", "fr": "Aucun événement à venir n’est répertorié. Je peux vous aider à contacter l’équipe événements.", "ja": "現在、今後のイベントは登録されていません。イベント担当者へのお問い合わせをお手伝いできます。"}[locale]
    cards = [card for card in (event_card(event) for event in events) if card['registration_open'] is not False][:3]
    return {'mode': 'database_answer', 'reply': f"## {heading}\n\n{note if cards else empty}", 'intent': {'code': 'events'},
            'events': cards, 'suggested_questions': [], 'next_actions': []}


async def common_answer(message, locale, session, user=None, conversation_brief=None):
    question = canonical_question(message)
    if question is None:
        return None
    if question == 'What events are coming up at CCIFJ?':
        return await _upcoming_events(locale, session)
    copy = {
        'en': {'fees':'Annual membership fees','tier':'Category','fee':'Annual fee','categories':'Available categories','known':'I can help compare eligibility for your situation.','unknown':'Not specified',
               'snapshot':'These are the fees in the current CCIFJ database snapshot. Confirm eligibility, current rates, taxes and any prorating with the membership team before applying.',
               'question':'Is your company established in Japan, or based outside Japan?',
               'join':'Choose the category that fits your company, then complete the official application. CCIFJ confirms eligibility and admission; this chat does not submit the application.',
               'form':'Official membership application','empty':'Membership fees are not available in the database. I can help prepare a question for the membership team.',
               'book':'Choose an event first; I can check its registration deadline before you share attendee details. Saving a booking requires login, each attendee’s name and email, and your explicit approval. No payment is taken. Which event interests you?',
               'newsletter':'Choose General News, Training Announcements or the @StudioCCIFJ podcast. Subscription requires login and a review card that you approve before anything is saved. Which newsletter would you like?'},
        'fr': {'fees':'Cotisations annuelles','tier':'Catégorie','fee':'Cotisation annuelle','categories':'Catégories disponibles','known':'Je peux vous aider à comparer l’éligibilité selon votre situation.','unknown':'Non précisé',
               'snapshot':'Ces montants proviennent des données CCIFJ enregistrées. Confirmez l’éligibilité, les tarifs actuels, les taxes et tout prorata avec l’équipe adhésion avant de déposer votre demande.',
               'question':'Votre entreprise est-elle établie au Japon ou basée à l’étranger ?',
               'join':'Choisissez la catégorie adaptée à votre entreprise, puis remplissez le formulaire officiel. La CCIFJ confirme l’éligibilité et l’admission ; ce chat ne dépose pas votre demande.',
               'form':'Formulaire officiel d’adhésion','empty':'Les cotisations ne sont pas disponibles dans la base. Je peux vous aider à préparer une question pour l’équipe adhésion.',
               'book':'Choisissez d’abord un événement : je peux vérifier la date limite d’inscription. L’enregistrement nécessite une connexion, le nom et l’e-mail de chaque participant, puis votre approbation explicite. Aucun paiement n’est effectué. Quel événement vous intéresse ?',
               'newsletter':'Choisissez les actualités générales, les annonces de formation ou le podcast @StudioCCIFJ. L’inscription nécessite une connexion et votre approbation de la carte récapitulative avant tout enregistrement. Quelle newsletter souhaitez-vous ?'},
        'ja': {'fees':'年間会費','tier':'会員区分','fee':'年間会費','categories':'会員区分','known':'御社の状況に応じた入会資格の比較をお手伝いできます。','unknown':'記載なし',
               'snapshot':'CCIFJデータベースに登録された会費です。申請前に、入会資格・最新の会費・税・月割りの有無を会員担当者にご確認ください。',
               'question':'御社は日本に拠点がありますか。それとも日本国外に所在していますか？',
               'join':'会社の状況に合う区分を確認し、公式フォームから申請してください。入会資格と承認はCCIFJが確認します。このチャットから申請は送信されません。',
               'form':'公式入会申請フォーム','empty':'データベースに会費情報がありません。会員担当者への質問の準備をお手伝いできます。',
               'book':'まずイベントを選び、申込期限を確認しましょう。予約の保存にはログイン、参加者全員の氏名・メールアドレス、内容確認後の明示的な承認が必要です。支払いは発生しません。どのイベントをご希望ですか？',
               'newsletter':'一般ニュース、研修案内、@StudioCCIFJポッドキャストから選べます。保存前にログインと確認カードの承認が必要です。どのニュースレターをご希望ですか？'},
    }[locale]
    result = {'mode':'database_answer','suggested_questions':[], 'next_actions':[]}
    if question in {'What are the CCIFJ membership fees?', 'How do I become a CCIFJ member?'}:
        categories = await crud.list_member_categories(session)
        if not categories:
            return {**result,'reply':copy['empty']}
        def amount(category):
            if category.annual_price is None or not category.currency:
                return copy['unknown']
            value = f'{category.annual_price:,.2f}'.rstrip('0').rstrip('.')
            return f'{value} {category.currency}'
        table = f"| {copy['tier']} | {copy['fee']} |\n| --- | --- |\n" + '\n'.join(f'| {c.category_name} | {amount(c)} |' for c in categories)
        question_text = copy['known'] if (conversation_brief or {}).get('japan_presence') else copy['question']
        result['sources'] = [{'title':copy['fees'],'url':MEMBERSHIP_FEE_SOURCE,'provenance':'database','record_type':'member_category'}]
        if question == 'What are the CCIFJ membership fees?':
            result['reply'] = f"### {copy['fees']}\n\n{table}\n\n{copy['snapshot']}\n\n{question_text}"
        else:
            url=membership_form(locale)
            result['reply'] = f"{copy['join']}\n\n**{copy['categories']}:** {', '.join(c.category_name for c in categories)}.\n\n[{copy['form']}]({url})"
            result['actions'] = [{'type':'external_link','label':copy['form'],'url':url}]
        result['suggested_questions'] = [localized_question(q, locale) for q in (
            'What are the benefits of CCIFJ membership?',
            'How do I become a CCIFJ member?' if question == 'What are the CCIFJ membership fees?' else 'What are the CCIFJ membership fees?',
        )]
        result['intent']={'code':'membership'}
        return result
    if question == 'How can I book a ticket for a CCIFJ event?':
        return {**result,'reply':copy['book'],'intent':{'code':'events'}}
    if question == 'How do I subscribe to the CCIFJ newsletter?':
        return {**result,'reply':copy['newsletter'],'intent':{'code':'newsletter'}}
    return None
