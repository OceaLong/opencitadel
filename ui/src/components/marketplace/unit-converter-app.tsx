"use client";

import { useMemo, useState } from "react";
import { ArrowLeftRight } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type UnitCategory = "length" | "weight" | "temperature" | "storage" | "area";

type UnitDef = {
  id: string;
  label: string;
  toBase: (value: number) => number;
  fromBase: (value: number) => number;
};

const UNIT_GROUPS: Record<UnitCategory, UnitDef[]> = {
  length: [
    { id: "m", label: "米 (m)", toBase: (v) => v, fromBase: (v) => v },
    { id: "km", label: "千米 (km)", toBase: (v) => v * 1000, fromBase: (v) => v / 1000 },
    { id: "cm", label: "厘米 (cm)", toBase: (v) => v / 100, fromBase: (v) => v * 100 },
    { id: "mm", label: "毫米 (mm)", toBase: (v) => v / 1000, fromBase: (v) => v * 1000 },
    { id: "mi", label: "英里 (mi)", toBase: (v) => v * 1609.344, fromBase: (v) => v / 1609.344 },
    { id: "ft", label: "英尺 (ft)", toBase: (v) => v * 0.3048, fromBase: (v) => v / 0.3048 },
    { id: "in", label: "英寸 (in)", toBase: (v) => v * 0.0254, fromBase: (v) => v / 0.0254 },
  ],
  weight: [
    { id: "kg", label: "千克 (kg)", toBase: (v) => v, fromBase: (v) => v },
    { id: "g", label: "克 (g)", toBase: (v) => v / 1000, fromBase: (v) => v * 1000 },
    { id: "lb", label: "磅 (lb)", toBase: (v) => v * 0.453592, fromBase: (v) => v / 0.453592 },
    { id: "oz", label: "盎司 (oz)", toBase: (v) => v * 0.0283495, fromBase: (v) => v / 0.0283495 },
    { id: "t", label: "吨 (t)", toBase: (v) => v * 1000, fromBase: (v) => v / 1000 },
  ],
  temperature: [
    { id: "c", label: "摄氏度 (°C)", toBase: (v) => v, fromBase: (v) => v },
    {
      id: "f",
      label: "华氏度 (°F)",
      toBase: (v) => ((v - 32) * 5) / 9,
      fromBase: (v) => (v * 9) / 5 + 32,
    },
    { id: "k", label: "开尔文 (K)", toBase: (v) => v - 273.15, fromBase: (v) => v + 273.15 },
  ],
  storage: [
    { id: "b", label: "字节 (B)", toBase: (v) => v, fromBase: (v) => v },
    { id: "kb", label: "KB", toBase: (v) => v * 1024, fromBase: (v) => v / 1024 },
    { id: "mb", label: "MB", toBase: (v) => v * 1024 ** 2, fromBase: (v) => v / 1024 ** 2 },
    { id: "gb", label: "GB", toBase: (v) => v * 1024 ** 3, fromBase: (v) => v / 1024 ** 3 },
    { id: "tb", label: "TB", toBase: (v) => v * 1024 ** 4, fromBase: (v) => v / 1024 ** 4 },
  ],
  area: [
    { id: "m2", label: "平方米 (m²)", toBase: (v) => v, fromBase: (v) => v },
    { id: "km2", label: "平方千米 (km²)", toBase: (v) => v * 1_000_000, fromBase: (v) => v / 1_000_000 },
    { id: "ha", label: "公顷 (ha)", toBase: (v) => v * 10_000, fromBase: (v) => v / 10_000 },
    { id: "acre", label: "英亩 (acre)", toBase: (v) => v * 4046.86, fromBase: (v) => v / 4046.86 },
    { id: "ft2", label: "平方英尺 (ft²)", toBase: (v) => v * 0.092903, fromBase: (v) => v / 0.092903 },
  ],
};

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return "";
  const rounded = Math.abs(value) < 0.0001 || Math.abs(value) >= 1_000_000
    ? value.toExponential(6)
    : Number(value.toPrecision(10));
  return String(rounded);
}

function convert(value: number, from: UnitDef, to: UnitDef): number {
  const base = from.toBase(value);
  return to.fromBase(base);
}

function parseInitialValue(text: string): number | null {
  const match = text.match(/(-?\d+(?:\.\d+)?)/);
  if (!match) return null;
  const parsed = Number(match[1]);
  return Number.isFinite(parsed) ? parsed : null;
}

function resolveInitialCategory(text: string): UnitCategory {
  const lowered = text.toLowerCase();
  if (lowered.includes("华氏") || lowered.includes("f")) return "temperature";
  if (lowered.includes("gb") || lowered.includes("mb")) return "storage";
  if (lowered.includes("公斤") || lowered.includes("磅")) return "weight";
  if (lowered.includes("英里") || lowered.includes("公里")) return "length";
  return "length";
}

function resolveInitialInput(text: string): string {
  const parsed = parseInitialValue(text);
  return parsed !== null ? String(parsed) : "1";
}

export function UnitConverterApp({ initialText = "" }: { initialText?: string }) {
  const [category, setCategory] = useState<UnitCategory>(() => resolveInitialCategory(initialText));
  const initialUnits = UNIT_GROUPS[resolveInitialCategory(initialText)];
  const [fromUnit, setFromUnit] = useState(initialUnits[0].id);
  const [toUnit, setToUnit] = useState(initialUnits[1]?.id ?? initialUnits[0].id);
  const [inputValue, setInputValue] = useState(() => resolveInitialInput(initialText));

  const handleCategoryChange = (value: UnitCategory) => {
    setCategory(value);
    const units = UNIT_GROUPS[value];
    setFromUnit(units[0].id);
    setToUnit(units[1]?.id ?? units[0].id);
  };

  const units = UNIT_GROUPS[category];
  const fromDef = units.find((unit) => unit.id === fromUnit) ?? units[0];
  const toDef = units.find((unit) => unit.id === toUnit) ?? units[1] ?? units[0];

  const result = useMemo(() => {
    const value = Number(inputValue);
    if (!Number.isFinite(value)) return "";
    return formatNumber(convert(value, fromDef, toDef));
  }, [fromDef, inputValue, toDef]);

  const swap = () => {
    setFromUnit(toUnit);
    setToUnit(fromUnit);
    if (result) setInputValue(result);
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">单位换算器</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          长度、重量、温度、存储与面积常用单位实时互转
        </p>
      </div>

      <Tabs value={category} onValueChange={(value) => handleCategoryChange(value as UnitCategory)}>
        <TabsList className="grid w-full grid-cols-5">
          <TabsTrigger value="length">长度</TabsTrigger>
          <TabsTrigger value="weight">重量</TabsTrigger>
          <TabsTrigger value="temperature">温度</TabsTrigger>
          <TabsTrigger value="storage">存储</TabsTrigger>
          <TabsTrigger value="area">面积</TabsTrigger>
        </TabsList>

        {(["length", "weight", "temperature", "storage", "area"] as UnitCategory[]).map((tab) => (
          <TabsContent key={tab} value={tab}>
            <Card>
              <CardContent className="space-y-4 py-5">
                <div className="grid gap-4 md:grid-cols-[1fr_auto_1fr] md:items-end">
                  <div className="space-y-2">
                    <Label htmlFor={`from-${tab}`}>从</Label>
                    <Input
                      id={`from-${tab}`}
                      type="number"
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                    />
                    <Select value={fromUnit} onValueChange={setFromUnit}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {units.map((unit) => (
                          <SelectItem key={unit.id} value={unit.id}>
                            {unit.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <button
                    type="button"
                    onClick={swap}
                    className="border-border/70 bg-background hover:bg-muted/60 mx-auto flex size-10 items-center justify-center rounded-full border transition-colors"
                    aria-label="交换单位"
                  >
                    <ArrowLeftRight className="size-4" />
                  </button>

                  <div className="space-y-2">
                    <Label htmlFor={`to-${tab}`}>到</Label>
                    <Input id={`to-${tab}`} readOnly value={result} />
                    <Select value={toUnit} onValueChange={setToUnit}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {units.map((unit) => (
                          <SelectItem key={unit.id} value={unit.id}>
                            {unit.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
