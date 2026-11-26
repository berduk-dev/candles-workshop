import { expect, type Page, test } from "@playwright/test";

async function login(page: Page, user: string) {
  await page.goto("/");
  await page.getByLabel("Логин").fill(user);
  await page.getByLabel("Пароль").fill("demo123");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("link", { name: "Ассистент" })).toBeVisible();
}

test("поступление → задание через ассистента → подтверждение", async ({ page }) => {
  // Кладовщик оформляет поступление и принимает партию.
  await login(page, "storekeeper");
  await page.goto("/receipts/new");
  const supplier = `ООО «Проверка ${Date.now()}»`;
  await page.getByLabel("Поставщик").fill(supplier);
  await page.getByLabel("Материал").selectOption({ label: "Воск соевый, кг" });
  await page.getByPlaceholder("Количество").fill("12,5");
  await page.getByPlaceholder("Цена").fill("320");
  await page.getByRole("button", { name: "Оформить поступление" }).click();

  await expect(page.getByRole("heading", { name: "Реестр партий" })).toBeVisible();
  await page.getByLabel("Фильтр supplier").fill(supplier);
  const row = page.getByRole("row").filter({ hasText: supplier });
  await expect(row).toContainText("12,5 кг");
  await expect(row).toContainText("На проверке");
  await row.getByRole("link").click();
  await page.getByRole("button", { name: "Принять" }).click();
  await expect(page.getByText("Принята", { exact: true }).first()).toBeVisible();

  // Кладовщику нельзя создавать задания.
  await page.goto("/jobs");
  await expect(page.getByRole("button", { name: "Новое задание" })).toHaveCount(0);
  await page.getByRole("button", { name: "Выйти" }).click();
  // Ждём форму входа: переход раньше времени оборвал бы запрос выхода.
  await expect(page.getByLabel("Логин")).toBeVisible();

  // Технолог просит ассистента запланировать задание.
  await login(page, "technologist");
  await page.getByRole("button", { name: "Начать диалог" }).click();
  await page.getByLabel("Вопрос ассистенту").fill("Запланируй 7 свечей «Лаванда»");
  await page.getByRole("button", { name: "Отправить" }).click();
  const card = page.getByLabel("Предложение ассистента");
  await expect(card).toContainText("Свеча «Лаванда», 7 шт");
  await expect(card).toContainText("До подтверждения ничего не изменится");

  // До подтверждения задания нет, после — есть, с зелёным светофором.
  await card.getByRole("button", { name: "Подтвердить" }).click();
  await expect(card).toContainText("создано и запланировано");
  const number = /З-\d{6}/.exec((await card.textContent()) ?? "")?.[0];
  await page.getByRole("link", { name: "Задания", exact: true }).click();
  await page.getByRole("link", { name: number }).click();
  await expect(page.getByText("Запланировано").first()).toBeVisible();
  await expect(page.locator('[data-light="green"]')).toHaveCount(7);
});
