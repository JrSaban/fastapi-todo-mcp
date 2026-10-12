"""DTOs et Validation avec Pydantic V2."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator


class UserBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr = Field(..., max_length=255, description="Email de l'utilisateur")
    first_name: str = Field(..., max_length=100, description="Prénom de l'utilisateur")
    last_name: str = Field(..., max_length=100, description="Nom de l'utilisateur")
    pseudo: str | None = Field(default=None, max_length=50, description="Pseudo de l'utilisateur")

    @field_validator("email")
    @classmethod
    def lower_case_email(cls, value: str) -> str:
        """Convertit l'email en minuscules."""
        return value.lower()


class UserCreate(UserBase):
    """Payload reçu lors d'un POST /users."""

    password: str = Field(
        ..., min_length=6, max_length=255, description="Mot de passe de l'utilisateur"
    )
    confirm_password: str = Field(
        ..., min_length=6, max_length=255, description="Confirmation du mot de passe"
    )

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, value: str, info) -> str:
        """Vérifie que le mot de passe et sa confirmation correspondent."""
        if value != info.data["password"]:
            raise ValueError("Les mots de passe ne correspondent pas.")
        return value


class UserUpdate(BaseModel):
    """Payload reçu lors d'un PATCH /users/{id}."""

    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str | None = Field(
        default=None, max_length=100, description="Prénom de l'utilisateur"
    )
    last_name: str | None = Field(default=None, max_length=100, description="Nom de l'utilisateur")
    pseudo: str | None = Field(default=None, max_length=50, description="Pseudo de l'utilisateur")

    @field_validator("first_name", "last_name")
    @classmethod
    def prevent_explicit_none(cls, value: str | None, info) -> str | None:
        """Rejette la requête si le client envoie explicitement la clé avec la valeur null."""
        if value is None:
            raise ValueError(f"Le champ '{info.field_name}' ne peut pas être null s'il est fourni.")
        return value


class UserEmailUpdate(BaseModel):
    """Payload when an admin wants to change a user's email."""

    model_config = ConfigDict(str_strip_whitespace=True)

    new_email: EmailStr = Field(..., max_length=255, description="Nouvel email de l'utilisateur")

    @field_validator("new_email")
    @classmethod
    def lower_case_email(cls, value: str) -> str:
        """Converts the email to lowercase."""
        return value.lower()


class UserSelfEmailUpdate(UserEmailUpdate):
    """Payload when a user wants to change his email."""

    current_password: str = Field(
        ..., max_length=255, description="Mot de passe actuel de l'utilisateur"
    )


class UserPasswordUpdate(BaseModel):
    """Payload reçu lors d'un PATCH /users/me/password."""

    model_config = ConfigDict(str_strip_whitespace=True)

    old_password: str = Field(
        ..., min_length=6, max_length=255, description="Ancien mot de passe de l'utilisateur"
    )
    new_password: str = Field(
        ..., min_length=6, max_length=255, description="Nouveau mot de passe de l'utilisateur"
    )
    confirm_new_password: str = Field(
        ..., min_length=6, max_length=255, description="Confirmation du nouveau mot de passe"
    )
    revoke_api_tokens: bool = Field(
        default=False,
        description="Révoque tous vos tokens API existants.",
    )

    @field_validator("confirm_new_password")
    @classmethod
    def passwords_match(cls, value: str, info) -> str:
        """Checks that the new password and its confirmation match."""
        if value != info.data["new_password"]:
            raise ValueError("Les nouveaux mots de passe ne correspondent pas.")
        return value


class UserResponse(UserBase):
    """Payload sérialisé et retourné au client HTTP."""

    id: int
    is_active: bool
    is_admin: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    model_config = ConfigDict(from_attributes=True)


class UserActiveStatusUpdate(BaseModel):
    is_active: bool = Field(..., description="Statut actif de l'utilisateur")


class UserAdminStatusUpdate(BaseModel):
    is_admin: bool = Field(..., description="Statut admin de l'utilisateur")
