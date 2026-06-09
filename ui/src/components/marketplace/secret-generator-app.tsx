"use client";

import { useCallback, useState } from "react";
import { Copy, RefreshCw, Shield } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const CHARSETS = {
  lowercase: "abcdefghijklmnopqrstuvwxyz",
  uppercase: "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  digits: "0123456789",
  symbols: "!@#$%^&*()-_=+[]{}",
};

function buildCharset(options: {
  lowercase: boolean;
  uppercase: boolean;
  digits: boolean;
  symbols: boolean;
}): string {
  let charset = "";
  if (options.lowercase) charset += CHARSETS.lowercase;
  if (options.uppercase) charset += CHARSETS.uppercase;
  if (options.digits) charset += CHARSETS.digits;
  if (options.symbols) charset += CHARSETS.symbols;
  return charset;
}

function generatePassword(length: number, charset: string): string {
  const values = new Uint32Array(length);
  crypto.getRandomValues(values);
  return Array.from(values, (value) => charset[value % charset.length]).join("");
}

function generateUuid(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function SecretGeneratorApp({ initialLength }: { initialLength?: number }) {
  const [length, setLength] = useState(initialLength ?? 16);
  const [options, setOptions] = useState({
    lowercase: true,
    uppercase: true,
    digits: true,
    symbols: true,
  });
  const [password, setPassword] = useState("");
  const [uuidCount, setUuidCount] = useState(5);
  const [uuids, setUuids] = useState<string[]>([]);

  const generateNewPassword = useCallback(() => {
    const charset = buildCharset(options);
    if (!charset) {
      toast.error("请至少选择一种字符类型");
      return;
    }
    const safeLength = Math.max(4, Math.min(128, length));
    setPassword(generatePassword(safeLength, charset));
  }, [length, options]);

  const generateNewUuids = useCallback(() => {
    const count = Math.max(1, Math.min(50, uuidCount));
    setUuids(Array.from({ length: count }, () => generateUuid()));
  }, [uuidCount]);

  const copy = async (value: string) => {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    toast.message("已复制到剪贴板");
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">密码 & UUID 生成器</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          基于浏览器加密随机数生成强密码与 UUID，本地计算不上传
        </p>
      </div>

      <Tabs defaultValue="password">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="password">密码</TabsTrigger>
          <TabsTrigger value="uuid">UUID</TabsTrigger>
        </TabsList>

        <TabsContent value="password" className="space-y-4">
          <Card>
            <CardContent className="space-y-4 py-5">
              <div className="space-y-2">
                <Label htmlFor="password-length">长度</Label>
                <Input
                  id="password-length"
                  type="number"
                  min={4}
                  max={128}
                  value={length}
                  onChange={(e) => setLength(Number(e.target.value) || 16)}
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {(
                  [
                    ["lowercase", "小写字母"],
                    ["uppercase", "大写字母"],
                    ["digits", "数字"],
                    ["symbols", "符号"],
                  ] as const
                ).map(([key, label]) => (
                  <label key={key} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={options[key]}
                      onCheckedChange={(checked) =>
                        setOptions((prev) => ({ ...prev, [key]: checked === true }))
                      }
                    />
                    {label}
                  </label>
                ))}
              </div>
              <Button onClick={generateNewPassword}>
                <RefreshCw className="size-4" />
                生成密码
              </Button>
            </CardContent>
          </Card>

          <Card className="border-primary/20 bg-primary/5">
            <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Shield className="size-4" />
                生成结果
              </CardTitle>
              <Button variant="outline" size="sm" onClick={() => copy(password)} disabled={!password}>
                <Copy className="size-4" />
                复制
              </Button>
            </CardHeader>
            <CardContent>
              <pre className="border-border/70 bg-background/80 text-foreground overflow-auto rounded-xl border p-4 font-mono text-sm break-all">
                {password || "点击生成密码"}
              </pre>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="uuid" className="space-y-4">
          <Card>
            <CardContent className="space-y-4 py-5">
              <div className="space-y-2">
                <Label htmlFor="uuid-count">数量</Label>
                <Input
                  id="uuid-count"
                  type="number"
                  min={1}
                  max={50}
                  value={uuidCount}
                  onChange={(e) => setUuidCount(Number(e.target.value) || 5)}
                />
              </div>
              <Button onClick={generateNewUuids}>
                <RefreshCw className="size-4" />
                生成 UUID
              </Button>
            </CardContent>
          </Card>

          <Card className="border-primary/20 bg-primary/5">
            <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
              <CardTitle className="text-base">UUID 列表</CardTitle>
              <Button
                variant="outline"
                size="sm"
                onClick={() => copy(uuids.join("\n"))}
                disabled={uuids.length === 0}
              >
                <Copy className="size-4" />
                复制全部
              </Button>
            </CardHeader>
            <CardContent>
              <pre className="border-border/70 bg-background/80 text-foreground overflow-auto rounded-xl border p-4 font-mono text-sm leading-relaxed whitespace-pre-wrap">
                {uuids.length > 0 ? uuids.join("\n") : "点击生成 UUID"}
              </pre>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
