"""Typed, truthful recovery for turns known not to have started a write."""
from secrets import token_hex


class ReadOnlyTurnFailure(RuntimeError):
    def __init__(self, *, brief=None, code='answer_unavailable', reference=None):
        super().__init__(code)
        self.code = code
        self.reference = reference or token_hex(6)
        self.brief = brief or {}


def recovery_response(error, locale):
    text = {
        'en': "I couldn’t complete that answer right now. This request did not submit a booking, subscription or staff inquiry. You can continue in this conversation or ask me to help prepare an inquiry for CCIFJ staff.",
        'fr': "Je n’ai pas pu terminer cette réponse pour le moment. Cette demande n’a soumis aucune réservation, inscription à une newsletter ni demande au personnel. Vous pouvez continuer cette conversation ou me demander de préparer une question pour l’équipe CCIFJ.",
        'ja': "現在、この回答を完了できませんでした。今回のリクエストで予約・ニュースレター登録・スタッフへの問い合わせは送信されていません。この会話を続けるか、CCIFJスタッフへの質問の準備をご依頼ください。",
    }.get(locale)
    return {'mode':'recovery', 'reply':text, 'request_id':error.reference,
            'warnings':['answer_unavailable'], 'sources':[], 'next_actions':[],
            'agent_state':{'conversation_brief':error.brief}}
