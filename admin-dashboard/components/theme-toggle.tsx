"use client"

import { useEffect, useState } from "react"
import { useTheme } from "next-themes"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  if (!mounted) return null

  const isDark = resolvedTheme === "dark"
  return (
    <div className="flex items-center gap-2">
      <Label htmlFor="theme-toggle" className="text-xs text-muted-foreground">Dark</Label>
      <Switch
        id="theme-toggle"
        checked={isDark}
        onCheckedChange={(v) => setTheme(v ? "dark" : "light")}
      />
    </div>
  )
}
