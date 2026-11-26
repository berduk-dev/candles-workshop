from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.errors import DomainError, NotFoundError
from app.models import Material, Recipe, RecipeItem, User
from app.schemas.inventory import MaterialOut
from app.schemas.production import RecipeIn, RecipeItemOut, RecipeOut
from app.services import audit
from app.services.permissions import Perm, require

_WITH_ITEMS = selectinload(Recipe.items).selectinload(RecipeItem.material)


def material_out(m: Material) -> MaterialOut:
    return MaterialOut(id=m.id, code=m.code, name=m.name, unit=m.unit)


def to_recipe_out(recipe: Recipe) -> RecipeOut:
    return RecipeOut(
        id=recipe.id,
        name=recipe.name,
        items=[
            RecipeItemOut(material=material_out(i.material), qty_per_unit=i.qty_per_unit)
            for i in recipe.items
        ],
    )


async def list_recipes(session: AsyncSession, user: User) -> list[RecipeOut]:
    require(user, Perm.VIEW_STOCK)
    recipes = await session.scalars(select(Recipe).options(_WITH_ITEMS).order_by(Recipe.name))
    return [to_recipe_out(r) for r in recipes]


async def load(session: AsyncSession, recipe_id: int) -> Recipe:
    recipe = await session.scalar(
        select(Recipe)
        .options(_WITH_ITEMS)
        .where(Recipe.id == recipe_id)
        .execution_options(populate_existing=True)
    )
    if recipe is None:
        raise NotFoundError("Рецепт не найден.")
    return recipe


async def get(session: AsyncSession, user: User, recipe_id: int) -> RecipeOut:
    require(user, Perm.VIEW_STOCK)
    return to_recipe_out(await load(session, recipe_id))


async def find_by_name(session: AsyncSession, user: User, name: str) -> RecipeOut:
    """Поиск рецепта по названию: «лаванда» найдёт «Свеча «Лаванда»»."""
    recipes = await list_recipes(session, user)
    wanted = name.strip().lower()
    exact = [r for r in recipes if r.name.lower() == wanted]
    found = exact or [r for r in recipes if wanted in r.name.lower() or r.name.lower() in wanted]
    if len(found) == 1:
        return found[0]
    names = ", ".join(r.name for r in (found or recipes))
    if not found:
        raise NotFoundError(f"Рецепт «{name}» не найден. Есть рецепты: {names}.")
    raise DomainError(f"Под название «{name}» подходит несколько рецептов: {names}. Уточните.")


async def save(session: AsyncSession, user: User, data: RecipeIn, recipe_id: int | None = None) -> RecipeOut:
    """Создаёт рецепт или заменяет существующий целиком."""
    require(user, Perm.RECIPE_EDIT)
    codes = {item.material_code for item in data.items}
    materials = {m.code: m for m in await session.scalars(select(Material).where(Material.code.in_(codes)))}
    if missing := sorted(codes - materials.keys()):
        raise NotFoundError(f"Не найдены материалы с кодами: {', '.join(missing)}.")
    taken = await session.scalar(select(Recipe.id).where(Recipe.name == data.name))
    if taken and taken != recipe_id:
        raise DomainError(f"Рецепт «{data.name}» уже есть.")

    before = None
    if recipe_id is None:
        recipe = Recipe(name=data.name, items=[])
        session.add(recipe)
    else:
        recipe = await load(session, recipe_id)
        before = _audit_view(to_recipe_out(recipe))
        recipe.name = data.name
        recipe.items.clear()
        await session.flush()  # сначала удаляем старые строки, иначе сработает уникальность
    recipe.items.extend(
        RecipeItem(material_id=materials[i.material_code].id, qty_per_unit=i.qty_per_unit) for i in data.items
    )
    await session.flush()
    result = to_recipe_out(await load(session, recipe.id))
    audit.record(
        session,
        user,
        entity="recipe",
        entity_id=recipe.id,
        action="updated" if before else "created",
        before=before,
        after=_audit_view(result),
    )
    return result


def _audit_view(recipe: RecipeOut) -> dict:
    return {
        "name": recipe.name,
        "items": {i.material.code: str(i.qty_per_unit) for i in recipe.items},
    }
