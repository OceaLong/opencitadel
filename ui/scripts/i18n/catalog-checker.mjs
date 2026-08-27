import ts from "typescript";

const TRANSLATOR_FACTORIES = new Set(["useTranslations", "getTranslations"]);
const TRANSLATOR_METHODS = new Set(["markup", "raw", "rich"]);
const USER_FACING_ATTRIBUTES = new Set([
  "alt",
  "aria-label",
  "description",
  "label",
  "placeholder",
  "title",
]);
const RUNTIME_KEY_PROPERTIES = new Set(["errorKey", "error_key", "i18n_key"]);
const TOAST_METHODS = new Set(["error", "info", "success", "warning"]);
const NON_TRANSLATABLE_TAGS = new Set(["code", "kbd", "pre", "samp"]);

function flattenCatalog(value, prefix = "", keys = []) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    if (prefix) keys.push(prefix);
    return keys;
  }

  for (const [key, child] of Object.entries(value)) {
    flattenCatalog(child, prefix ? `${prefix}.${key}` : key, keys);
  }
  return keys;
}

function unwrapExpression(node) {
  let current = node;
  while (
    ts.isAwaitExpression(current) ||
    ts.isParenthesizedExpression(current) ||
    ts.isAsExpression(current) ||
    ts.isTypeAssertionExpression(current) ||
    ts.isNonNullExpression(current) ||
    ts.isSatisfiesExpression(current)
  ) {
    current = current.expression;
  }
  return current;
}

function factoryName(expression) {
  const target = unwrapExpression(expression);
  if (ts.isIdentifier(target)) return target.text;
  if (ts.isPropertyAccessExpression(target)) return target.name.text;
  return undefined;
}

function staticString(node) {
  const target = unwrapExpression(node);
  if (ts.isStringLiteralLike(target) || ts.isNoSubstitutionTemplateLiteral(target)) {
    return target.text;
  }
  return undefined;
}

function namespaceArgument(call) {
  const argument = call.arguments[0];
  if (!argument) return undefined;
  const direct = staticString(argument);
  if (direct !== undefined) return direct;

  const target = unwrapExpression(argument);
  if (!ts.isObjectLiteralExpression(target)) return undefined;
  for (const property of target.properties) {
    if (!ts.isPropertyAssignment(property)) continue;
    const name = property.name && property.name.getText().replaceAll(/["']/g, "");
    if (name === "namespace") return staticString(property.initializer);
  }
  return undefined;
}

function translatorNamespace(initializer) {
  if (!initializer) return undefined;
  const target = unwrapExpression(initializer);
  if (!ts.isCallExpression(target)) return undefined;
  const name = factoryName(target.expression);
  if (!TRANSLATOR_FACTORIES.has(name)) return undefined;
  return namespaceArgument(target);
}

function translatorFactoryCall(initializer) {
  if (!initializer) return undefined;
  const target = unwrapExpression(initializer);
  if (!ts.isCallExpression(target)) return undefined;
  const name = factoryName(target.expression);
  return TRANSLATOR_FACTORIES.has(name) ? { call: target, name } : undefined;
}

function translatorNamespaceFromType(typeNode) {
  if (!typeNode) return undefined;
  const namespaces = new Set();
  function visitType(node) {
    if (
      ts.isTypeQueryNode(node) &&
      node.exprName.getText().endsWith("useTranslations") &&
      node.typeArguments?.length === 1
    ) {
      const argument = node.typeArguments[0];
      if (ts.isLiteralTypeNode(argument) && ts.isStringLiteral(argument.literal)) {
        namespaces.add(argument.literal.text);
      }
    }
    ts.forEachChild(node, visitType);
  }
  visitType(typeNode);
  return namespaces.size === 1 ? [...namespaces][0] : undefined;
}

function templateText(node, sourceFile) {
  const target = unwrapExpression(node);
  const literal = staticString(target);
  if (literal !== undefined) return { dynamic: false, text: literal };

  if (ts.isTemplateExpression(target)) {
    let text = target.head.text;
    for (const span of target.templateSpans) {
      text += `\${${span.expression.getText(sourceFile)}}${span.literal.text}`;
    }
    return { dynamic: true, text };
  }

  return { dynamic: true, text: target.getText(sourceFile) };
}

function lineOf(sourceFile, node) {
  return sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
}

function propertyName(node) {
  if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node) || ts.isStringLiteralLike(node)) {
    return node.text;
  }
  return undefined;
}

function isRuntimeKeyProperty(node) {
  const target = unwrapExpression(node);
  if (ts.isPropertyAccessExpression(target)) {
    return RUNTIME_KEY_PROPERTIES.has(target.name.text);
  }
  if (ts.isElementAccessExpression(target) && target.argumentExpression) {
    const name = staticString(target.argumentExpression);
    return name !== undefined && RUNTIME_KEY_PROPERTIES.has(name);
  }
  return false;
}

function normalizeText(text) {
  return text.replaceAll(/\s+/g, " ").trim();
}

function isMeaningfulUserText(text) {
  const normalized = normalizeText(text);
  if (!normalized) return false;
  if (/^(?:https?:\/\/|mailto:|tel:)/i.test(normalized)) return false;
  if (/^[\p{P}\p{S}\s]+$/u.test(normalized)) return false;
  return /[\p{L}\p{N}]/u.test(normalized);
}

function isTestFixture(path) {
  return /(?:^|\/)__tests__(?:\/|$)|\.(?:spec|test)\.[cm]?[jt]sx?$/.test(path);
}

function openingElement(node) {
  if (ts.isJsxElement(node)) return node.openingElement;
  if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) return node;
  return undefined;
}

function isExplicitlyNonTranslatable(node) {
  let current = node;
  while (current) {
    const opening = openingElement(current);
    if (opening) {
      const tagName = opening.tagName.getText().split(".").at(-1).toLowerCase();
      if (NON_TRANSLATABLE_TAGS.has(tagName)) return true;
      const translateAttribute = opening.attributes.properties.find(
        (attribute) => ts.isJsxAttribute(attribute) && propertyName(attribute.name) === "translate",
      );
      if (
        translateAttribute &&
        ts.isJsxAttribute(translateAttribute) &&
        translateAttribute.initializer &&
        ts.isStringLiteral(translateAttribute.initializer) &&
        translateAttribute.initializer.text === "no"
      ) {
        return true;
      }
    }
    current = current.parent;
  }
  return false;
}

function dynamicExpansionId(expansion) {
  return `${expansion.namespace ?? "<full>"} :: ${expansion.template}`;
}

function validateExpansion(expansion) {
  if (!expansion || typeof expansion !== "object") {
    throw new TypeError("dynamic expansion must be an object");
  }
  if (typeof expansion.template !== "string" || !expansion.template) {
    throw new TypeError("dynamic expansion template must be a non-empty string");
  }
  if (!Array.isArray(expansion.keys) || expansion.keys.length === 0) {
    throw new TypeError(`dynamic expansion ${dynamicExpansionId(expansion)} must declare keys`);
  }
  if (!expansion.keys.every((key) => typeof key === "string" && key.length > 0)) {
    throw new TypeError(
      `dynamic expansion ${dynamicExpansionId(expansion)} contains an invalid key`,
    );
  }
}

/**
 * Analyze locale catalogs and TypeScript/JavaScript sources as one strict contract.
 *
 * Dynamic expansions are explicit finite registrations shaped as
 * `{ namespace, template, keys }`. `namespace` is omitted for full-key
 * `translate(...)` calls. Every registration must match a real source call.
 */
export function analyzeCatalog({ locales, sourceFiles, dynamicExpansions = [] }) {
  if (!locales || typeof locales !== "object" || Object.keys(locales).length < 2) {
    throw new TypeError("at least two locale catalogs are required");
  }
  if (!Array.isArray(sourceFiles)) {
    throw new TypeError("sourceFiles must be an array");
  }

  for (const expansion of dynamicExpansions) validateExpansion(expansion);

  const localeNames = Object.keys(locales).sort();
  const localeKeys = new Map(
    localeNames.map((locale) => [locale, new Set(flattenCatalog(locales[locale]))]),
  );
  const allKeys = new Set([...localeKeys.values()].flatMap((keys) => [...keys]));
  const sharedKeys = new Set(
    [...allKeys].filter((key) => localeNames.every((locale) => localeKeys.get(locale).has(key))),
  );
  const usedKeys = new Set();
  const unknownDynamicCalls = [];
  const hardcodedFindings = [];
  const consumedExpansions = new Set();

  function consumeKey({ namespace, key, path, sourceFile, node }) {
    const parsed = templateText(key, sourceFile);
    if (!parsed.dynamic) {
      usedKeys.add(namespace ? `${namespace}.${parsed.text}` : parsed.text);
      return;
    }

    const matching = dynamicExpansions.filter(
      (expansion) =>
        (expansion.namespace ?? undefined) === namespace && expansion.template === parsed.text,
    );
    if (matching.length === 0) {
      unknownDynamicCalls.push(
        `${path}:${lineOf(sourceFile, node)} ${namespace ?? "<full>"} :: ${parsed.text}`,
      );
      return;
    }

    for (const expansion of matching) {
      consumedExpansions.add(expansion);
      for (const expandedKey of expansion.keys) {
        usedKeys.add(namespace ? `${namespace}.${expandedKey}` : expandedKey);
      }
    }
  }

  for (const sourceFileInput of sourceFiles) {
    if (
      !sourceFileInput ||
      typeof sourceFileInput.path !== "string" ||
      typeof sourceFileInput.source !== "string"
    ) {
      throw new TypeError("each source file must provide string path and source fields");
    }
    const { path, source } = sourceFileInput;
    const scriptKind = /\.[cm]?[jt]sx$/.test(path) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const sourceFile = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, scriptKind);
    const scopes = [new Map()];
    const scanHardcoded = !isTestFixture(path);

    function lookupBinding(name) {
      for (let index = scopes.length - 1; index >= 0; index -= 1) {
        if (scopes[index].has(name)) return scopes[index].get(name);
      }
      return undefined;
    }

    function bindName(name, binding = { kind: "other" }) {
      if (ts.isIdentifier(name)) {
        scopes.at(-1).set(name.text, binding);
        return;
      }
      if (ts.isObjectBindingPattern(name) || ts.isArrayBindingPattern(name)) {
        for (const element of name.elements) {
          if (ts.isBindingElement(element)) bindName(element.name);
        }
      }
    }

    function visit(node) {
      const createsScope =
        node !== sourceFile &&
        (ts.isFunctionLike(node) || ts.isBlock(node) || ts.isCatchClause(node));
      if (createsScope) scopes.push(new Map());

      if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
        const isTranslateModule = /(?:^|\/)i18n\/translate$/.test(node.moduleSpecifier.text);
        const bindings = node.importClause?.namedBindings;
        if (isTranslateModule && bindings && ts.isNamedImports(bindings)) {
          for (const element of bindings.elements) {
            if ((element.propertyName ?? element.name).text === "translate") {
              bindName(element.name, { kind: "full" });
            }
          }
        }
      }

      if (ts.isFunctionLike(node)) {
        for (const parameter of node.parameters) {
          const namespace = translatorNamespaceFromType(parameter.type);
          bindName(
            parameter.name,
            namespace === undefined ? { kind: "other" } : { kind: "translator", namespace },
          );
        }
      }

      if (ts.isVariableDeclaration(node)) {
        const factory = translatorFactoryCall(node.initializer);
        const namespace = translatorNamespace(node.initializer);
        if (factory && namespace === undefined) {
          const argument = factory.call.arguments[0];
          unknownDynamicCalls.push(
            `${path}:${lineOf(sourceFile, factory.call)} ${factory.name} :: ${argument ? argument.getText(sourceFile) : "<missing>"}`,
          );
        }
        bindName(
          node.name,
          namespace === undefined ? { kind: "other" } : { kind: "translator", namespace },
        );
      }

      if (ts.isCallExpression(node) && node.arguments.length > 0) {
        const callTarget = unwrapExpression(node.expression);
        const directName = factoryName(callTarget);
        const directBinding =
          ts.isIdentifier(callTarget) && lookupBinding(callTarget.text)?.kind === "full";
        if (directName === "translate" && directBinding) {
          consumeKey({
            namespace: undefined,
            key: node.arguments[0],
            path,
            sourceFile,
            node,
          });
        } else {
          let translatorName;
          if (ts.isIdentifier(callTarget)) {
            translatorName = callTarget.text;
          } else if (
            ts.isPropertyAccessExpression(callTarget) &&
            TRANSLATOR_METHODS.has(callTarget.name.text) &&
            ts.isIdentifier(unwrapExpression(callTarget.expression))
          ) {
            translatorName = unwrapExpression(callTarget.expression).text;
          }
          const binding = translatorName && lookupBinding(translatorName);
          if (binding?.kind === "translator") {
            consumeKey({
              namespace: binding.namespace,
              key: node.arguments[0],
              path,
              sourceFile,
              node,
            });
          }
        }

        if (
          scanHardcoded &&
          ts.isPropertyAccessExpression(callTarget) &&
          ts.isIdentifier(callTarget.expression) &&
          callTarget.expression.text === "toast" &&
          TOAST_METHODS.has(callTarget.name.text)
        ) {
          const message = staticString(node.arguments[0]);
          if (message !== undefined && isMeaningfulUserText(message)) {
            hardcodedFindings.push(
              `${path}:${lineOf(sourceFile, node)} toast.${callTarget.name.text}: ${normalizeText(message)}`,
            );
          }
        }
      }

      if (ts.isPropertyAssignment(node)) {
        const name = propertyName(node.name);
        const key = staticString(node.initializer);
        if (name && RUNTIME_KEY_PROPERTIES.has(name) && key !== undefined) usedKeys.add(key);
      }

      if (ts.isBinaryExpression(node)) {
        let value;
        if (isRuntimeKeyProperty(node.left)) value = staticString(node.right);
        if (value === undefined && isRuntimeKeyProperty(node.right))
          value = staticString(node.left);
        if (value !== undefined) usedKeys.add(value);
      }

      if (scanHardcoded && ts.isJsxText(node) && !isExplicitlyNonTranslatable(node)) {
        const message = normalizeText(node.text);
        if (isMeaningfulUserText(message)) {
          hardcodedFindings.push(`${path}:${lineOf(sourceFile, node)} JSX text: ${message}`);
        }
      }

      if (scanHardcoded && ts.isJsxAttribute(node) && !isExplicitlyNonTranslatable(node)) {
        const name = propertyName(node.name);
        if (name && USER_FACING_ATTRIBUTES.has(name) && node.initializer) {
          const message = ts.isStringLiteral(node.initializer) ? node.initializer.text : undefined;
          if (message !== undefined && isMeaningfulUserText(message)) {
            hardcodedFindings.push(
              `${path}:${lineOf(sourceFile, node)} ${name}: ${normalizeText(message)}`,
            );
          }
        }
      }

      if (
        scanHardcoded &&
        ts.isJsxExpression(node) &&
        !isExplicitlyNonTranslatable(node) &&
        node.expression &&
        staticString(node.expression) !== undefined
      ) {
        const message = staticString(node.expression);
        if (isMeaningfulUserText(message)) {
          hardcodedFindings.push(
            `${path}:${lineOf(sourceFile, node)} JSX expression: ${normalizeText(message)}`,
          );
        }
      }

      ts.forEachChild(node, visit);
      if (createsScope) scopes.pop();
    }

    visit(sourceFile);
  }

  const localeMismatches = [...allKeys]
    .filter((key) => !localeNames.every((locale) => localeKeys.get(locale).has(key)))
    .map((key) => {
      const present = localeNames.filter((locale) => localeKeys.get(locale).has(key));
      return `${key}: ${present.join(", ")}`;
    })
    .sort();
  const missingKeys = [...usedKeys]
    .map((key) => {
      const missing = localeNames.filter((locale) => !localeKeys.get(locale).has(key));
      return missing.length > 0 ? `${key}: ${missing.join(", ")}` : undefined;
    })
    .filter(Boolean)
    .sort();
  const unusedKeys = [...sharedKeys].filter((key) => !usedKeys.has(key)).sort();
  const orphanDynamicExpansions = dynamicExpansions
    .filter((expansion) => !consumedExpansions.has(expansion))
    .map(dynamicExpansionId)
    .sort();

  return {
    localeMismatches,
    missingKeys,
    unusedKeys,
    unknownDynamicCalls: unknownDynamicCalls.sort(),
    orphanDynamicExpansions,
    hardcodedFindings: hardcodedFindings.sort(),
  };
}

export function assertCatalogClean(report) {
  const failures = [
    ["locale mismatches", report.localeMismatches],
    ["missing keys", report.missingKeys],
    ["unused keys", report.unusedKeys],
    ["unknown dynamic calls", report.unknownDynamicCalls],
    ["orphan dynamic expansions", report.orphanDynamicExpansions],
    ["hardcoded UI strings", report.hardcodedFindings],
  ].filter(([, findings]) => findings.length > 0);

  if (failures.length === 0) return;
  throw new Error(
    failures
      .map(
        ([category, findings]) =>
          `${category}:\n${findings.map((item) => `  - ${item}`).join("\n")}`,
      )
      .join("\n"),
  );
}
