"""Tâches de maintenance en ligne de commande.

    python -m app.taches recalculer_verifications [--chantier ID]
    python -m app.taches nettoyer_brouillons
    python -m app.taches envoyer_resumes_quotidiens
    python -m app.taches verifier_inactivite
    python -m app.taches expirer_releves
    python -m app.taches renvoyer_emails_echoues

Les quatre dernières seront lancées par une tâche planifiée sur le serveur (une fois par jour pour
le résumé et l'inactivité, toutes les heures pour les relevés et les e-mails en échec).
"""

import argparse
import sys
import uuid

from app.database import SessionLocal
from app.emails.envoi import renvoyer_echoues
from app.emails.notifications import (
    envoyer_resumes_quotidiens,
    expirer_et_notifier_releves,
    verifier_inactivite,
)
from app.geo.verification import verifier_photos
from app.rapports import nettoyer_brouillons


def recalculer_verifications(chantier_id: uuid.UUID | None = None) -> int:
    with SessionLocal() as db:
        nombre = verifier_photos(db, chantier_id=chantier_id)
        db.commit()
    return nombre


def pluriel(nombre: int, mot: str) -> str:
    return f"{nombre} {mot}{'s' if nombre > 1 else ''}"


def main(arguments: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        prog="python -m app.taches", description=__doc__.splitlines()[0]
    )
    taches = analyseur.add_subparsers(dest="tache", required=True)
    recalcul = taches.add_parser(
        "recalculer_verifications", help="recalcule distance, statut et alertes des photos"
    )
    recalcul.add_argument(
        "--chantier", type=uuid.UUID, metavar="ID", help="seulement les photos de ce chantier"
    )
    taches.add_parser(
        "nettoyer_brouillons", help="supprime les brouillons inactifs depuis plus de 7 jours"
    )
    taches.add_parser(
        "envoyer_resumes_quotidiens", help="un e-mail par propriétaire en mode résumé quotidien"
    )
    taches.add_parser(
        "verifier_inactivite", help="prévient les propriétaires d'un chantier resté sans nouvelles"
    )
    taches.add_parser(
        "expirer_releves",
        help="passe en « expiré » les liens de relevé dépassés et prévient leur auteur",
    )
    taches.add_parser(
        "renvoyer_emails_echoues", help="nouvelle tentative pour les e-mails en échec (3 au total)"
    )
    options = analyseur.parse_args(arguments)

    if options.tache == "recalculer_verifications":
        nombre = recalculer_verifications(options.chantier)
        print(f"{nombre} photo{'s' if nombre > 1 else ''} vérifiée{'s' if nombre > 1 else ''}.")
        return 0
    with SessionLocal() as db:
        if options.tache == "nettoyer_brouillons":
            rapports, photos = nettoyer_brouillons(db)
            print(f"{rapports} brouillon(s) supprimé(s), avec {photos} photo(s).")
        elif options.tache == "envoyer_resumes_quotidiens":
            print(f"{pluriel(envoyer_resumes_quotidiens(db), 'résumé')} envoyé(s).")
        elif options.tache == "verifier_inactivite":
            print(f"{pluriel(verifier_inactivite(db), 'alerte')} d'inactivité envoyée(s).")
        elif options.tache == "expirer_releves":
            print(f"{pluriel(expirer_et_notifier_releves(db), 'lien')} de relevé expiré(s).")
        elif options.tache == "renvoyer_emails_echoues":
            envoyes, echecs = renvoyer_echoues(db)
            print(f"{pluriel(envoyes, 'e-mail')} renvoyé(s), {echecs} toujours en échec.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
