"""The AICTE integration boundary.

STATUS: UNVERIFIED. No AICTE API, credential, webhook or specification has been made available to this project, and none is assumed to exist.
What is here is (1) a contract THIS PROJECT defines for what it would send and what it would need back (`contract.py`), (2) an adapter interface that keeps
that contract away from the domain model, (3) a mock adapter and a demo receiver, both labelled DEMO ONLY, and (4) an HTTP adapter that speaks the
contract to a configured URL. Whether a real AICTE endpoint implements it - or whether AICTE needs a different shape - is unknown until an authorised
party supplies the real interface and an authorised connection has actually been tested (docs/INTEGRATION.md).
"""
