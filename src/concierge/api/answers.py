"""Small deterministic answers for common, read-only concierge questions.

Prices and categories come from SQL on each request, never a cached FAQ or RAG
snapshot. These paths need no model initialization and perform no writes.
"""
from concierge.db.relationaldb import crud
from concierge.resources import MEMBERSHIP_FEE_SOURCE, membership_form
from .catalog import canonical_question


async def common_answer(message, locale, session, user=None, conversation_brief=None):
    question = canonical_question(message)
    if question is None:
        return None
    if question == 'What events are coming up at CCIFJ?':
        from .paths import run_path_step
        from .schemas import ChatRequest
        return await run_path_step(ChatRequest(message=message, locale=locale, path_step_id='events.upcoming'), user, session)
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
        result['intent']={'code':'membership'}
        return result
    if question == 'How can I book a ticket for a CCIFJ event?':
        return {**result,'reply':copy['book'],'intent':{'code':'events'}}
    if question == 'How do I subscribe to the CCIFJ newsletter?':
        return {**result,'reply':copy['newsletter'],'intent':{'code':'newsletter'}}
    return None
