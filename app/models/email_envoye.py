import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, cle_primaire_uuid, horodatage, type_enum


class TypeEmail(StrEnum):
    RAPPORT_PUBLIE = "rapport_publie"
    RESUME_QUOTIDIEN = "resume_quotidien"
    INACTIVITE = "inactivite"
    INVITATION_ACCEPTEE = "invitation_acceptee"
    RELEVE_RECU = "releve_recu"
    RELEVE_EXPIRE = "releve_expire"
    POSITION_MODIFIEE = "position_modifiee"
    MOT_DE_PASSE = "mot_de_passe"


class StatutEmail(StrEnum):
    EN_ATTENTE = "en_attente"
    ENVOYE = "envoye"
    ECHEC = "echec"


class EmailEnvoye(Base):
    """Journal des e-mails : chaque envoi, son statut et ses tentatives.

    Le contenu est conservé pour pouvoir renvoyer un e-mail en échec, sauf celui de réinitialisation
    du mot de passe (il contient le jeton en clair) : corps_texte et corps_html restent alors vides.
    """

    __tablename__ = "emails_envoyes"
    __table_args__ = (
        # Résumés et alertes d'inactivité déjà envoyés pour un chantier (une fois par période).
        Index("ix_emails_envoyes_type_chantier", "type", "chantier_id", "cree_le"),
    )

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    destinataire: Mapped[str] = mapped_column(String(255))
    type: Mapped[TypeEmail] = mapped_column(type_enum(TypeEmail, "type_email"))
    statut: Mapped[StatutEmail] = mapped_column(
        type_enum(StatutEmail, "statut_email"),
        default=StatutEmail.EN_ATTENTE,
        server_default=StatutEmail.EN_ATTENTE.value,
    )
    tentatives: Mapped[int] = mapped_column(default=0, server_default="0")
    derniere_erreur: Mapped[str | None] = mapped_column(Text)
    cree_le: Mapped[datetime] = horodatage()
    envoye_le: Mapped[datetime | None]
    sujet: Mapped[str] = mapped_column(String(300))
    corps_texte: Mapped[str | None] = mapped_column(Text)
    corps_html: Mapped[str | None] = mapped_column(Text)
    url_desinscription: Mapped[str | None] = mapped_column(String(500))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    chantier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chantiers.id", ondelete="SET NULL")
    )
