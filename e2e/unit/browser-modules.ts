import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import ts from 'typescript';
import type { Page } from '@playwright/test';

const LIB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../artifacts/chargeback-os/src/lib');
export const ORIGIN = 'http://unit.test';

/** Compiles a real frontend module to a browser ES module. Type-only imports are dropped. */
function compile(name: string): string {
  const source = readFileSync(path.join(LIB, `${name}.ts`), 'utf8')
    .replace(/import\.meta\.env\.BASE_URL/g, '"/"');
  return ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  }).outputText;
}

/** Opens a blank page on a fake origin that serves /lib/<name>.js from src/lib. */
export async function openModulePage(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route(`${ORIGIN}/**`, async route => {
    const url = new URL(route.request().url());
    const match = url.pathname.match(/^\/lib\/([a-z-]+)\.js$/);
    if (url.pathname === '/') {
      return route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>unit</title>' });
    }
    if (match) {
      return route.fulfill({ contentType: 'text/javascript', body: compile(match[1]) });
    }
    return route.fulfill({ status: 404, body: 'not found' });
  });
  await page.goto(`${ORIGIN}/`);
  return errors;
}
