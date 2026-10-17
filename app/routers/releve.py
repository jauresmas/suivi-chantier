"""Page du proche qui relève la position du terrain : ouverte avec le lien reçu, sans compte.

Le lien sert une seule fois : dès que les mesures sont enregistrées, il ne peut plus en envoyer
d'autres. Les photos du terrain restent possibles pendant une heure après le relevé.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user_optional
from app.database import get_db
from app.emails.notifications import notifier_releve_recu
from app.geo.releve import MESURES_MAX, PRECISION_MAX_M, ReleveInvalide, calculer, lire_mesures
from app.models import PhotoReleve, RelevePosition, User
from app.photos import stockage
from app.photos.traitement import TAILLE_MAX_OCTETS, ImageRefusee, traiter_image
from app.services.position import (
    PHOTOS_MAX,
    charger_par_jeton,
    enregistrer_mesures,
    photos_possibles,
    probleme_lien,
)
from app.templating import templates

router = APIRouter(tags=["relevé de position"], include_in_schema=False)

# Messages polis, sans détail technique : la personne n'a pas de compte et ne connaît pas
# l'application.
MESSAGES_LIEN = {
    "inconnu": (
        404,
        "Ce lien ne fonctionne pas",
        "Vérifiez qu'il a été copié en entier, "
        "ou demandez un nouveau lien à la personne qui vous l'a envoyé.",
    ),
    "annule": (
        410,
        "Ce lien n'est plus valable",
        "{proprietaire} l'a annulé. Si besoin, demandez-lui un nouveau lien.",
    ),
    "expire": (
        410,
        "Ce lien a expiré",
        "Il n'était valable que 3 jours. Demandez un nouveau lien à {proprietaire}.",
    ),
    "utilise": (
        410,
        "Ce lien a déjà servi",
        "La position du terrain a bien été envoyée à {proprietaire}. Merci pour votre aide !",
    ),
}


def nom_proprietaire(releve: RelevePosition | None) -> str:
    return releve.demandeur.nom_complet if releve and releve.demandeur else "Le propriétaire"


def message_lien(probleme: str, releve: RelevePosition | None) -> tuple[int, str, str]:
    code, titre, texte = MESSAGES_LIEN[probleme]
    return code, titre, texte.format(proprietaire=nom_proprietaire(releve))


@router.get("/releve/{jeton}", name="page_releve")
def page_releve(request: Request, jeton: str, db: Session = Depends(get_db)):
    releve = charger_par_jeton(db, jeton)
    if probleme := probleme_lien(releve):
        code, titre, texte = message_lien(probleme, releve)
        return templates.TemplateResponse(
            request,
            "releve/invalide.html",
            {"titre": titre, "texte": texte, "succes": probleme == "utilise"},
            status_code=code,
        )
    return templates.TemplateResponse(
        request,
        "releve/proche.html",
        {
            "releve": releve,
            "chantier": releve.chantier,
            "proprietaire": nom_proprietaire(releve),
            "jeton": jeton,
            "precision_max_m": PRECISION_MAX_M,
            "photos_max": PHOTOS_MAX,
        },
    )


class MesureBrute(BaseModel):
    lat: float
    lon: float
    precision: float
    t: float  # horodatage de la mesure, en millisecondes (horloge du téléphone)


class EnvoiMesures(BaseModel):
    mesures: list[MesureBrute] = Field(max_length=MESURES_MAX)
    horloge: float  # heure du téléphone au moment de l'envoi, en millisecondes
    forcer: bool = False  # « Envoyer quand même » après deux relevés au signal faible


def lien_utilisable(db: Session, jeton: str) -> RelevePosition:
    releve = charger_par_jeton(db, jeton, verrouiller=True)
    if probleme := probleme_lien(releve):
        db.rollback()
        code, titre, texte = message_lien(probleme, releve)
        raise HTTPException(code, f"{titre}. {texte}")
    return releve


@router.post("/releve/{jeton}/mesures")
def recevoir_mesures(
    request: Request,
    jeton: str,
    envoi: EnvoiMesures,
    taches: BackgroundTasks,
    utilisateur: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Calcule la position (côté serveur). Signal trop faible : rien n'est enregistré, sauf si
    `forcer`."""
    releve = lien_utilisable(db, jeton)
    try:
        mesures = lire_mesures(
            [(m.lat, m.lon, m.precision, m.t) for m in envoi.mesures],
            envoi.horloge,
            datetime.now(UTC),
            releve.cree_le,
        )
    except ReleveInvalide as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from None
    resultat = calculer(db, mesures)
    if resultat is None or (resultat.signal_faible and not envoi.forcer):
        db.rollback()
        # `exploitable` : au moins une mesure utilisable, donc « Envoyer quand même » possible.
        return {"statut": "signal_faible", "exploitable": resultat is not None}
    enregistrer_mesures(db, releve, resultat, request.headers.get("user-agent", ""), utilisateur)
    db.commit()
    notifier_releve_recu(db, taches, releve)  # toujours immédiat : le relevé attend une décision
    return JSONResponse({"statut": "recu", "photos_max": PHOTOS_MAX}, status_code=201)


@router.post("/releve/{jeton}/photos", status_code=201)
def recevoir_photo(jeton: str, fichier: UploadFile = File(...), db: Session = Depends(get_db)):
    releve = charger_par_jeton(db, jeton, verrouiller=True)
    if releve is None:
        raise HTTPException(404, "Ce lien ne fonctionne pas.")
    if not photos_possibles(releve):
        db.rollback()
        raise HTTPException(409, "Il n'est plus possible d'ajouter des photos.")
    donnees = fichier.file.read(TAILLE_MAX_OCTETS + 1)
    if len(donnees) > TAILLE_MAX_OCTETS:
        raise HTTPException(413, "Photo trop lourde.")
    try:
        image = traiter_image(
            donnees
        )  # ré-encodée : aucune métadonnée du téléphone n'est conservée
    except ImageRefusee as exc:
        raise HTTPException(415, str(exc)) from None

    photo_id = uuid.uuid4()
    chemin, chemin_miniature = stockage.chemins_photo_releve(
        releve.chantier_id, releve.id, photo_id
    )
    try:
        stockage.ecrire(chemin, image.contenu)
        stockage.ecrire(chemin_miniature, image.miniature)
        releve.photos.append(
            PhotoReleve(
                id=photo_id,
                chemin_fichier=chemin,
                chemin_miniature=chemin_miniature,
                largeur_px=image.largeur_px,
                hauteur_px=image.hauteur_px,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        stockage.supprimer(chemin)
        stockage.supprimer(chemin_miniature)
        raise
    return {"id": str(photo_id), "restantes": PHOTOS_MAX - len(releve.photos)}
