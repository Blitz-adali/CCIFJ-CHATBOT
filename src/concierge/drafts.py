"""Narrow email-header validation; no body rewriting, routing or submission."""
import re
from email.utils import getaddresses

_BLOCK = re.compile(r'(?P<fence>```|~~~)email\s*\n(?P<body>.*?)(?P=fence)', re.S | re.I)
_HEADER = re.compile(r'^To:[ \t]*(?P<value>[^\n]*)', re.M | re.I)
_EMAIL = re.compile(r'^[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+$')
_CHANGE_RECIPIENT = re.compile(r'\b(?:recipient|addressee|send to|address it to|destinataire)\b|宛先|送信先', re.I)
_EDIT = re.compile(r'\b(?:shorter|revise|update|change|replace|remove|add|keep|final|entire|current|shorten|raccourci|modifi|remplace|ajout|supprime)\b|短く|修正|変更|全文|最終', re.I)


def _valid_header(value):
    addresses = getaddresses([value])
    return bool(addresses) and all(_EMAIL.fullmatch(address) for _,address in addresses)


def ensure_recipient(text, history=(), tool_messages=(), locale='en'):
    """Fill a missing recipient only from one direct contact result or an edited draft.

    Never choose between multiple officers or substitute the sender's email.
    An explicit recipient change prevents silently restoring an old address.
    A placeholder remains visibly incomplete; this function cannot submit a draft.
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    latest = next((m.content for m in reversed(history) if isinstance(m,HumanMessage) and isinstance(m.content,str)), '')
    direct = []
    for message in tool_messages:
        if (isinstance(message,ToolMessage) and message.name == 'get_ccifj_contact'
                and getattr(message,'status',None) != 'error' and isinstance(message.content,str)):
            direct.extend(re.findall(r'(?im)^\s*(?:-\s*)?Email:\s*([^\s<>]+@[^\s<>]+\.[^\s<>]+)\s*$',message.content))
    direct = list(dict.fromkeys(direct))
    selected = direct[0] if len(direct)==1 else None
    if not selected and _EDIT.search(latest) and not _CHANGE_RECIPIENT.search(latest):
        # Use only the most recent draft; do not reach past an incomplete/new
        # draft to resurrect the recipient of a different earlier inquiry.
        for message in reversed(history):
            if not isinstance(message,AIMessage) or not isinstance(message.content,str):
                continue
            prior = _BLOCK.search(message.content)
            if prior:
                header = _HEADER.search(prior['body'].split('\n\n',1)[0])
                if header and _valid_header(header['value']):
                    selected = header['value']
                break
    missing = False
    placeholder = {'en':'[Recipient email — please confirm]', 'fr':'[E-mail du destinataire à confirmer]', 'ja':'[宛先メールアドレスを確認してください]'}.get(locale,'[Recipient email — please confirm]')
    def repair(match):
        nonlocal missing
        body = match['body']
        header = _HEADER.search(body.split('\n\n',1)[0])
        if header and _valid_header(header['value']):
            return match[0]
        value = selected or placeholder
        if not selected:
            missing = True
        if header:
            body = body[:header.start()]+'To: '+value+body[header.end():]
        else:
            body = 'To: '+value+'\n'+body
        return match['fence']+'email\n'+body+match['fence']
    repaired = _BLOCK.sub(repair,text)
    if missing:
        notice = {'en':'The recipient email still needs confirmation; this draft is not ready to submit.',
                  'fr':'L’adresse e-mail du destinataire reste à confirmer ; ce brouillon n’est pas prêt à être soumis.',
                  'ja':'宛先メールアドレスの確認が必要です。この下書きはまだ送信依頼できません。'}.get(locale,'Recipient email needs confirmation before submission.')
        repaired += '\n\n'+notice
    return repaired, missing
