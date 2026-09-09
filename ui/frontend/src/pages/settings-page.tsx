import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  BrainIcon,
  LanguagesIcon,
  SendIcon,
  MicIcon,
  VideoIcon,
  ListIcon,
  DatabaseBackupIcon,
  SaveIcon,
  UploadIcon,
  Trash2Icon,
  DownloadIcon,
  UsersIcon,
  UtensilsIcon,
} from 'lucide-react'

import { api } from '@/lib/api'
import type { AppConfig } from '@/types'
import { useSession } from '@/hooks/use-session'
import { AccountCard } from '@/components/settings/account-card'
import { UsersCard } from '@/components/settings/users-card'

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'

type Draft = Partial<AppConfig>

function useDraft(loaded: AppConfig | undefined) {
  const [draft, setDraft] = useState<Draft>({})

  function get<K extends keyof AppConfig>(key: K): AppConfig[K] {
    if (key in draft) return draft[key] as AppConfig[K]
    return (loaded?.[key] ?? '') as AppConfig[K]
  }

  function set<K extends keyof AppConfig>(key: K, value: AppConfig[K]) {
    setDraft(prev => ({ ...prev, [key]: value }))
  }

  function reset() {
    setDraft({})
  }

  const dirty = Object.keys(draft).length > 0

  return { get, set, reset, draft, dirty }
}

function FieldHint({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-muted-foreground mt-1">{children}</p>
}

function Field({ label, hint, children }: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      {children}
      {hint && <FieldHint>{hint}</FieldHint>}
    </div>
  )
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const { data: session, isLoading: sessionLoading } = useSession()
  const isAdmin = session?.is_admin ?? false
  const localAuth = session?.local_auth_enabled ?? false

  const { data: loaded, isLoading: configLoading } = useQuery({
    queryKey: ['config'],
    queryFn: () => api.getConfig(),
    // Instance configuration holds the API keys, so the server refuses it to
    // non-admins. Not asking avoids a pointless 403 on every visit.
    enabled: isAdmin,
  })

  const { get, set, reset, draft, dirty } = useDraft(loaded)

  const [saving, setSaving] = useState(false)
  const [cookiesUploading, setCookiesUploading] = useState(false)
  const cookiesFileRef = useRef<HTMLInputElement>(null)
  const importFileRef = useRef<HTMLInputElement>(null)

  async function handleSave() {
    if (!dirty) return
    setSaving(true)
    try {
      await api.saveConfig(draft)
      toast.success('Settings saved')
      reset()
      await queryClient.invalidateQueries({ queryKey: ['config'] })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  async function handleCookiesUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setCookiesUploading(true)
    try {
      const result = await api.uploadCookies(file)
      toast.success(result.message)
      await queryClient.invalidateQueries({ queryKey: ['config'] })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to upload cookies file')
    } finally {
      setCookiesUploading(false)
    }
  }

  async function handleDeleteCookies() {
    try {
      const result = await api.deleteCookies()
      toast.success(result.message)
      await queryClient.invalidateQueries({ queryKey: ['config'] })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete cookies')
    }
  }

  async function handleExport() {
    try {
      const data = await api.exportSettings()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const date = new Date().toISOString().slice(0, 10).replace(/-/g, '')
      a.href = url
      a.download = `settings-${date}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to export settings')
    }
  }

  async function handleImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    try {
      const text = await file.text()
      const parsed: unknown = JSON.parse(text)
      await api.importSettings(parsed)
      toast.success('Settings imported successfully')
      reset()
      await queryClient.invalidateQueries({ queryKey: ['config'] })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to import settings')
    }
  }

  const provider = get('llm_provider') || 'openai'
  const mealieEnabled = get('mealie_enabled') === 'true'
  const tandoorEnabled = get('tandoor_enabled') === 'true'
  const confirmBeforeUpload = get('confirm_before_upload') === 'true'
  const hasCookiesFile = Boolean(loaded?.yt_dlp_cookies_file)

  if (sessionLoading || (isAdmin && configLoading)) {
    return (
      <div className="p-6 text-muted-foreground text-sm">Loading settings…</div>
    )
  }

  // Non-admins get their own account and nothing else: everything below is
  // instance-wide, and the server refuses it to them anyway.
  if (!isAdmin) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-6 flex flex-col gap-6">
        <div>
          <h1 className="text-xl font-semibold">Settings</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Extraction settings are managed by an administrator.
          </p>
        </div>
        {localAuth && session && <AccountCard username={session.user} />}
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-6 pb-24 flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">Configure your Pick-a-Recipe application</p>
      </div>

      {/* AI Provider */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BrainIcon className="size-4" />
            AI Provider
          </CardTitle>
          <CardDescription>
            Used to turn raw video/page content into a structured recipe.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Field label="Provider">
            <Select
              value={provider}
              onValueChange={v => set('llm_provider', v as 'openai' | 'gemini')}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="openai">OpenAI</SelectItem>
                <SelectItem value="gemini">Google Gemini</SelectItem>
              </SelectContent>
            </Select>
          </Field>

          {provider === 'openai' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="OpenAI API Key">
                <Input
                  type="password"
                  placeholder="sk-..."
                  autoComplete="off"
                  value={get('openai_api_key')}
                  onChange={e => set('openai_api_key', e.target.value)}
                />
              </Field>
              <Field label="OpenAI Model">
                <Input
                  type="text"
                  placeholder="gpt-4"
                  autoComplete="off"
                  value={get('openai_model')}
                  onChange={e => set('openai_model', e.target.value)}
                />
              </Field>
            </div>
          )}

          {provider === 'gemini' && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Gemini API Key">
                <Input
                  type="password"
                  placeholder="AIza..."
                  autoComplete="off"
                  value={get('gemini_api_key')}
                  onChange={e => set('gemini_api_key', e.target.value)}
                />
              </Field>
              <Field label="Gemini Model">
                <Input
                  type="text"
                  placeholder="gemini-2.5-flash"
                  autoComplete="off"
                  value={get('gemini_model')}
                  onChange={e => set('gemini_model', e.target.value)}
                />
              </Field>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Nutrition */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UtensilsIcon className="size-4" />
            Nutrition lookup
          </CardTitle>
          <CardDescription>
            Optional live USDA FoodData Central search when an ingredient is missing from the local table.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Field
            label="USDA FoodData Central API Key"
            hint={
              <>
                Leave blank to keep using the local table. If this field is empty, the{' '}
                <code className="font-mono text-xs">USDA_FDC_API_KEY</code> environment variable is used instead. Get a free key at{' '}
                <a
                  href="https://fdc.nal.usda.gov/api-key-signup.html"
                  target="_blank"
                  rel="noreferrer"
                  className="underline underline-offset-2 hover:text-foreground"
                >
                  fdc.nal.usda.gov
                </a>
                . Stored in this instance&apos;s database, never in source code.
              </>
            }
          >
            <Input
              type="password"
              placeholder="USDA API key"
              autoComplete="off"
              value={get('usda_fdc_api_key')}
              onChange={e => set('usda_fdc_api_key', e.target.value)}
            />
          </Field>
        </CardContent>
      </Card>

      {/* Recipe Language */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <LanguagesIcon className="size-4" />
            Recipe Language
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field
            label="Recipe Language"
            hint="Language the final recipe is written in."
          >
            <Input
              type="text"
              placeholder="hebrew"
              value={get('recipe_lang')}
              onChange={e => set('recipe_lang', e.target.value)}
            />
          </Field>
          <Field
            label="Transcription Code"
            hint={
              <>
                Two-letter code matching the recipe language (e.g. <code className="font-mono text-xs">he</code>, <code className="font-mono text-xs">en</code>).
              </>
            }
          >
            <Input
              type="text"
              placeholder="he"
              value={get('target_language')}
              onChange={e => set('target_language', e.target.value)}
            />
          </Field>
        </CardContent>
      </Card>

      {/* Recipe Export */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <SendIcon className="size-4" />
            Recipe Export
          </CardTitle>
          <CardDescription>
            Where finished recipes get uploaded. Enable both to send every recipe to both managers.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Mealie */}
            <Card className={mealieEnabled ? '' : 'opacity-60'}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm">Mealie</CardTitle>
                  <Switch
                    checked={mealieEnabled}
                    onCheckedChange={checked =>
                      set('mealie_enabled', checked ? 'true' : 'false')
                    }
                    aria-label="Enable Mealie export"
                  />
                </div>
                <CardDescription className="text-xs">
                  {mealieEnabled ? 'Enabled' : 'Disabled'}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                <Field label="Mealie URL">
                  <Input
                    type="url"
                    placeholder="http://localhost:9925"
                    autoComplete="off"
                    value={get('mealie_host')}
                    onChange={e => set('mealie_host', e.target.value)}
                  />
                </Field>
                <Field label="Mealie API Key">
                  <Input
                    type="password"
                    placeholder="Your Mealie API key"
                    autoComplete="off"
                    value={get('mealie_api_key')}
                    onChange={e => set('mealie_api_key', e.target.value)}
                  />
                </Field>
              </CardContent>
            </Card>

            {/* Tandoor */}
            <Card className={tandoorEnabled ? '' : 'opacity-60'}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm">Tandoor</CardTitle>
                  <Switch
                    checked={tandoorEnabled}
                    onCheckedChange={checked =>
                      set('tandoor_enabled', checked ? 'true' : 'false')
                    }
                    aria-label="Enable Tandoor export"
                  />
                </div>
                <CardDescription className="text-xs">
                  {tandoorEnabled ? 'Enabled' : 'Disabled'}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                <Field label="Tandoor URL">
                  <Input
                    type="url"
                    placeholder="https://tandoor.example.com"
                    autoComplete="off"
                    value={get('tandoor_host')}
                    onChange={e => set('tandoor_host', e.target.value)}
                  />
                </Field>
                <Field label="Tandoor API Key">
                  <Input
                    type="password"
                    placeholder="Your Tandoor API key"
                    autoComplete="off"
                    value={get('tandoor_api_key')}
                    onChange={e => set('tandoor_api_key', e.target.value)}
                  />
                </Field>
              </CardContent>
            </Card>
          </div>

          {!mealieEnabled && !tandoorEnabled && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
              <strong>No recipe manager is enabled.</strong> Recipes will not be uploaded anywhere until you enable Mealie or Tandoor.
            </div>
          )}

          <div className="flex items-center gap-3">
            <Switch
              id="confirm_before_upload"
              checked={confirmBeforeUpload}
              onCheckedChange={checked =>
                set('confirm_before_upload', checked ? 'true' : 'false')
              }
            />
            <div>
              <Label htmlFor="confirm_before_upload">Ask me before uploading</Label>
              <FieldHint>Show a preview of the recipe first, then upload once you confirm.</FieldHint>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Whisper */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MicIcon className="size-4" />
            Whisper Transcription
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Field
            label="Whisper Model"
            hint="Larger models are more accurate but slower. 'Small' is recommended for mixed-language content."
          >
            <Select
              value={get('whisper_model') || 'small'}
              onValueChange={v => set('whisper_model', v)}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tiny">Tiny (fastest, least accurate)</SelectItem>
                <SelectItem value="base">Base</SelectItem>
                <SelectItem value="small">Small (recommended)</SelectItem>
                <SelectItem value="medium">Medium</SelectItem>
                <SelectItem value="large-v3">Large v3 (slowest, most accurate)</SelectItem>
              </SelectContent>
            </Select>
          </Field>

          <Field
            label="HuggingFace Token"
            hint={
              <>
                Optional. Enables faster model downloads and higher rate limits. Get one at{' '}
                <a
                  href="https://huggingface.co/settings/tokens"
                  target="_blank"
                  rel="noreferrer"
                  className="underline underline-offset-2 hover:text-foreground"
                >
                  huggingface.co/settings/tokens
                </a>
              </>
            }
          >
            <Input
              type="password"
              placeholder="hf_..."
              value={get('hf_token')}
              onChange={e => set('hf_token', e.target.value)}
            />
          </Field>
        </CardContent>
      </Card>

      {/* Video Downloads */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <VideoIcon className="size-4" />
            Video Downloads (YouTube, Instagram, etc.)
          </CardTitle>
          <CardDescription>
            Configure authentication when downloads fail. YouTube may show bot-check errors; Instagram may block anonymous requests.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Field
            label="Extract Cookies from Browser"
            hint="Automatically extract cookies from a browser installed on the server. Only works if Pick-a-Recipe runs on the same machine as your browser (not in Docker)."
          >
            <Select
              value={get('yt_dlp_cookies_browser') || ''}
              onValueChange={v => set('yt_dlp_cookies_browser', v)}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="None (disabled)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">None (disabled)</SelectItem>
                <SelectItem value="chrome">Chrome</SelectItem>
                <SelectItem value="firefox">Firefox</SelectItem>
                <SelectItem value="safari">Safari</SelectItem>
                <SelectItem value="edge">Edge</SelectItem>
                <SelectItem value="opera">Opera</SelectItem>
                <SelectItem value="brave">Brave</SelectItem>
                <SelectItem value="vivaldi">Vivaldi</SelectItem>
                <SelectItem value="chromium">Chromium</SelectItem>
              </SelectContent>
            </Select>
          </Field>

          <div className="flex flex-col gap-1.5">
            <Label>Upload Cookies File</Label>
            <div className="flex items-center gap-3 flex-wrap">
              <Button
                variant="outline"
                size="sm"
                disabled={cookiesUploading}
                onClick={() => cookiesFileRef.current?.click()}
              >
                <UploadIcon />
                {cookiesUploading ? 'Uploading…' : 'Choose cookies.txt file'}
              </Button>
              {hasCookiesFile && (
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button variant="destructive" size="sm">
                      <Trash2Icon />
                      Delete cookies
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Delete cookies file?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This will remove the uploaded cookies file. Downloads that required authentication may start failing.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction variant="destructive" onClick={handleDeleteCookies}>
                        Delete
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              )}
              <span className="text-sm text-muted-foreground">
                {hasCookiesFile ? 'Cookies file configured' : 'No cookies file uploaded'}
              </span>
            </div>
            <input
              ref={cookiesFileRef}
              type="file"
              accept=".txt"
              className="hidden"
              onChange={handleCookiesUpload}
            />
            <FieldHint>
              Upload a Netscape-format cookies.txt file exported from your browser.{' '}
              <a
                href="https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2 hover:text-foreground"
              >
                How to export cookies
              </a>
            </FieldHint>
          </div>

          <div className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
            <p className="font-medium text-foreground mb-1">How to get cookies:</p>
            <ol className="list-decimal list-inside space-y-0.5">
              <li>Install a browser extension like "Get cookies.txt LOCALLY" or "cookies.txt"</li>
              <li>Log into the video site in your browser (YouTube, Instagram, etc.)</li>
              <li>Use the extension to export cookies for that site</li>
              <li>Click "Choose cookies.txt file" above to upload the exported file</li>
            </ol>
          </div>
        </CardContent>
      </Card>

      {/* Queue */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ListIcon className="size-4" />
            Queue
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Field
            label="Max Concurrent Jobs"
            hint="Override with the MAX_CONCURRENT_JOBS environment variable."
          >
            <Input
              type="number"
              min={1}
              max={16}
              value={get('max_concurrent_jobs') || '3'}
              onChange={e => {
                const clamped = Math.min(16, Math.max(1, Number(e.target.value)))
                set('max_concurrent_jobs', String(clamped))
              }}
              className="w-28"
            />
          </Field>
        </CardContent>
      </Card>

      {localAuth && session && (
        <>
          <AccountCard username={session.user} />
          <UsersCard currentUsername={session.user} />
        </>
      )}

      {!localAuth && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <UsersIcon className="size-4" />
              Accounts
            </CardTitle>
            <CardDescription>
              Sign-in, accounts and admin rights come from Authentik. Add or
              remove people there, and use its groups to decide who administers
              this instance.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {/* Backup */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <DatabaseBackupIcon className="size-4" />
            Backup Settings
          </CardTitle>
          <CardDescription>
            Download your settings as a JSON file or restore them from a previous backup.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-4">
            <div className="flex flex-col gap-1.5">
              <Button variant="outline" onClick={handleExport}>
                <DownloadIcon />
                Export
              </Button>
              <FieldHint>Saves everything, including API keys, to a file on your device.</FieldHint>
            </div>
            <div className="flex flex-col gap-1.5">
              <Button variant="outline" onClick={() => importFileRef.current?.click()}>
                <UploadIcon />
                Import
              </Button>
              <FieldHint>Restore from a previously exported file.</FieldHint>
              <input
                ref={importFileRef}
                type="file"
                accept=".json"
                className="hidden"
                onChange={handleImport}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Sticky save bar */}
      {dirty && (
        <div className="fixed bottom-0 left-0 right-0 z-40 flex justify-end border-t border-border bg-background/90 px-4 py-3 backdrop-blur-sm">
          <Button onClick={handleSave} disabled={saving}>
            <SaveIcon />
            {saving ? 'Saving…' : 'Save Settings'}
          </Button>
        </div>
      )}
    </div>
  )
}
