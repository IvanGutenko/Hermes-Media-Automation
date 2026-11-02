"""Owner's topic boundaries for public Migratoria content."""
import re

SENSITIVE = re.compile(
    r'повест[кч]|военком|мобилизац|\bвоенн|\bвойн[аыуеой]|\bвойск|антивоен|'
    r'санкци|оппозиц|политическ|политзаключ|протест|репресси|'
    r'уголовн|нежелательн|иноагент|спецслужб|\bфсб\b|\bпутин|'
    r'\bследовател(?:ь|я|ю|ем|е|и|ей|ям|ями|ях)\b|под\s+страж|\bарест|задержан|допрос|правоохран|силовик|'
    r'экстремиз|террориз|судебн\w*\s+преслед|'
    r'правительств[аоуе]?\s+(?:рф|росси)|российск\w*\s+(?:власт|правительств)|'
    r'госдум|кремл|роскомнадзор|\bркн\b|минцифр|минпромторг|минтранс|ростех|совет\w*\s+федераци', re.I)


def allowed(text):
    return not SENSITIVE.search(text or '')


EDITORIAL_SCOPE = '''The owner lives in Russia and explicitly excludes sensitive topics.
Do not publish politics, the Russian government, conscription notices, military offices,
mobilization, war, sanctions, opposition, protests, repression, Russian government regulators
(including Roskomnadzor/RKN and censorship stories), security services or legal
persecution. Do not disguise such topics with neutral wording. Stay within the requested
channel's subject and choose an engaging, useful, non-political story.
A dramatic political headline is not an acceptable substitute for an interesting story.'''
