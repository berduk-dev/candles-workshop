import { useSearchParams } from "react-router";

/** Параметры адреса и их правка. Фильтры, сортировка и страница живут в адресе: ссылку можно
 *  отправить коллеге, «Назад» работает. */
export function useUrlParams() {
  const [params, setParams] = useSearchParams();

  function update(changes: Record<string, string>) {
    // Основа — адрес из окна, а не из рендера. Поля с паузой вызывают update позже, из старого рендера,
    // а setParams(fn) в react-router отдаёт функции адрес того же старого рендера (очереди, как
    // у setState, у него нет). Два фильтра подряд — «от» и сразу «до» — затирали бы друг друга.
    const next = new URLSearchParams(window.location.search);
    if (!("page" in changes)) next.delete("page"); // новый фильтр или сортировка — снова первая страница
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  }

  return [params, update] as const;
}
