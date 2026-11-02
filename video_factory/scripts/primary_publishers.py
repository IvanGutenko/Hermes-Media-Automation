"""Public primary publishers shared by HTTP and native-browser retrieval."""
DOMAINS=('gov.me','gov.uk','gov.si','europa.eu','geoconsul.gov.ge','mfa.gov.ge','mfa.am',
    'mfa.gov.rs','welcometoserbia.gov.rs','mfa.gov.tr','immigration.govt.nz',
    'immi.homeaffairs.gov.au','thaievisa.go.th','immigration.go.th','gov.kz','gov.uz',
    'pulkovoairport.ru','aeroflot.ru','flyone.eu','airserbia.com','etihad.com','belavia.by','s7.ru','visitsanya.com',
    'tourismthailand.org','thailandtourismdirectory.go.th','ihg.com','flypgs.com','turkishairlines.com',
    'service-public.gouv.fr','service-public.fr','france-visas.gouv.fr',
    'emirates.com','tbank.ru','qantas.com','klm.com','britishairways.com','aa.com',
    'hyatt.com','marriott.com','hilton.com','moominworld.fi','metsa-hanno.com',
    # CIS governments and flag carriers (gov.kz, gov.uz, mfa.am and belavia.by are listed above)
    'gov.kg','mfa.gov.kg','gov.az','gov.by','mfa.gov.by','gov.am','gov.md','gov.tj','mfa.tj',
    'airastana.com','flyarystan.com','uzairways.com','azal.az')


def primary_host(host):
    # The .gov registry verifies U.S. government eligibility (get.gov).
    return host.endswith('.gov') or any(host==d or host.endswith('.'+d) for d in DOMAINS)
