import { useEffect, useState } from "react";

export function sameValue(left: unknown, right: unknown): boolean {
  return Object.is(left, right) || JSON.stringify(left) === JSON.stringify(right);
}

export function useDraft<T extends object>(source: T) {
  const [changes, setChanges] = useState<Partial<T>>({});
  useEffect(() => {
    setChanges((current) => {
      const acknowledged = (Object.keys(current) as (keyof T)[]).filter((key) => sameValue(source[key], current[key]));
      if (!acknowledged.length) return current;
      const next = { ...current };
      for (const key of acknowledged) delete next[key];
      return next;
    });
  }, [source]);
  const dirtyKeys = (Object.keys(changes) as (keyof T)[]).filter((key) => !sameValue(source[key], changes[key]));
  const update = <K extends keyof T>(key: K, value: T[K]) => setChanges((current) => {
    const next = { ...current };
    if (sameValue(source[key], value)) delete next[key];
    else next[key] = value;
    return next;
  });
  const acknowledge = (submitted: Partial<T>) => setChanges((current) => {
    const next = { ...current };
    for (const key of Object.keys(submitted) as (keyof T)[]) {
      if (sameValue(next[key], submitted[key])) delete next[key];
    }
    return next;
  });
  const patch: Partial<T> = {};
  for (const key of dirtyKeys) patch[key] = changes[key];
  return {
    values: { ...source, ...changes }, dirtyKeys, dirty: dirtyKeys.length > 0,
    patch, update, acknowledge, reset: () => setChanges({}),
  };
}
