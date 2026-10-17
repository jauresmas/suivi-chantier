import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import CHAR, REAL, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, cle_primaire_uuid, horodatage, type_enum

if TYPE_CHECKING:
    from app.models.chantier import Chantier
    from app.models.user import User


class TypeReleveur(StrEnum):
    PROCHE = "proche"
    CHEF_CHANTIER = "chef_chantier"


class StatutReleve(StrEnum):
    EN_ATTENTE = "en_attente"
    RECU = "recu"
    VALIDE = "valide"
    REJETE = "rejete"
    EXPIRE = "expire"
    ANNULE = "annule"


class RelevePosition(Base):
    """Demande de relevé de la position du chantier, faite sur place par un proche (ou le chef de
    chantier).

    Le lien envoyé contient un jeton dont seule l'empreinte SHA-256 est stockée. Les mesures sont
    calculées par le serveur (app/geo/releve.py), jamais par le navigateur.
    """

    __tablename__ = "releves_position"
    __table_args__ = (
        # Un seul lien en attente à la fois par chantier, garanti par la base.
        Index(
            "un_releve_en_attente_par_chantier",
            "chantier_id",
            unique=True,
            postgresql_where=text("statut = 'en_attente'"),
        ),
    )

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    chantier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chantiers.id", ondelete="CASCADE"), index=True
    )
    demande_par: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    nom_releveur: Mapped[str] = mapped_column(String(150))
    lien_releveur: Mapped[str | None] = mapped_column(String(100))  # « tante », « cousin »…
    type_releveur: Mapped[TypeReleveur] = mapped_column(type_enum(TypeReleveur, "type_releveur"))
    empreinte_jeton: Mapped[str] = mapped_column(CHAR(64), unique=True)
    cree_le: Mapped[datetime] = horodatage()
    expire_le: Mapped[datetime]
    statut: Mapped[StatutReleve] = mapped_column(
        type_enum(StatutReleve, "statut_releve"),
        default=StatutReleve.EN_ATTENTE,
        server_default=StatutReleve.EN_ATTENTE.value,
    )
    # Résultat du relevé
    points_bruts: Mapped[WKBElement | None] = mapped_column(
        Geography("MULTIPOINT", srid=4326)
    )  # mesures retenues
    position_resultat: Mapped[WKBElement | None] = mapped_column(Geography("POINT", srid=4326))
    precision_estimee_m: Mapped[float | None] = mapped_column(REAL)
    nb_mesures: Mapped[int | None]
    duree_releve_s: Mapped[int | None]
    signal_faible: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    appareil: Mapped[str | None] = mapped_column(String(300))  # user-agent du navigateur
    realise_le: Mapped[datetime | None]
    # Compte connecté au moment du relevé, s'il y en avait un (un chef de chantier sur un lien «
    # proche » est signalé).
    realise_par: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    alertes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'")
    )
    # Décision du propriétaire
    traite_le: Mapped[datetime | None]
    traite_par: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    motif_rejet: Mapped[str | None] = mapped_column(Text)

    chantier: Mapped["Chantier"] = relationship()
    demandeur: Mapped["User | None"] = relationship(foreign_keys=[demande_par])
    realisateur: Mapped["User | None"] = relationship(foreign_keys=[realise_par])
    photos: Mapped[list["PhotoReleve"]] = relationship(
        back_populates="releve",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PhotoReleve.envoyee_le",
    )

    @property
    def statut_effectif(self) -> StatutReleve:
        """Un lien en attente dont les 72 h sont passées est expiré, même avant d'être marqué comme
        tel en base."""
        if self.statut == StatutReleve.EN_ATTENTE and self.expire_le <= datetime.now(UTC):
            return StatutReleve.EXPIRE
        return self.statut


class PhotoReleve(Base):
    """Photo du terrain prise par la personne qui fait le relevé (bornes, repère visible…)."""

    __tablename__ = "photos_releve"

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    releve_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("releves_position.id", ondelete="CASCADE"), index=True
    )
    chemin_fichier: Mapped[str] = mapped_column(String(500))
    chemin_miniature: Mapped[str] = mapped_column(String(500))
    largeur_px: Mapped[int]
    hauteur_px: Mapped[int]
    envoyee_le: Mapped[datetime] = horodatage()

    releve: Mapped["RelevePosition"] = relationship(back_populates="photos")
