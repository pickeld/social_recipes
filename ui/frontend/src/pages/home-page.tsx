import { useState, useCallback, useRef } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Link } from 'react-router'
import {
  SparklesIcon,
  LayersIcon,
  ChevronDownIcon,
  CheckCircle2Icon,
  ListChecksIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { JobCard } from '@/components/job-card'
import { RecipeEditForm } from '@/components/recipe-edit-form'
import { ImagePicker } from '@/components/image-picker'
import { api } from '@/lib/api'
import { useSocketEvent, useTransitionEvent } from '@/lib/socket'
import { useSession } from '@/hooks/use-session'
import type {
  Job,
  PendingUpload,
  RecipeData,
  JobProgressPayload,
  JobCompletePayload,
  JobFailedPayload,
  JobTransitionPayload,
} from '@/types'

interface PreviewState {
  uploadId: string
  pendingUpload: PendingUpload
  selectedImageIndex: number
  recipe: RecipeData
}

function isValidUrl(s: string): boolean {
  try {
    new URL(s)
    return true
  } catch {
    return false
  }
}

export function HomePage() {
  const queryClient = useQueryClient()
  const { data: session } = useSession()

  const sharedUrl =
    session?.shared_url ??
    new URLSearchParams(window.location.search).get('url') ??
    new URLSearchParams(window.location.search).get('text') ??
    ''

  const autoStart =
    (session?.auto_start === true) ||
    ['1', 'true', 'yes'].includes(
      new URLSearchParams(window.location.search).get('auto') ?? '',
    )

  const [urlInput, setUrlInput] = useState<string>(() => sharedUrl)
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchInput, setBatchInput] = useState('')
  const [previewState, setPreviewState] = useState<PreviewState | null>(null)

  const autoStartFiredRef = useRef(false)

  const { data: jobsData, isLoading: jobsLoading } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.listJobs(),
  })

  const jobs: Job[] = jobsData?.jobs ?? []
  const activeJobs = jobs.filter(
    (j) =>
      j.status === 'queued' ||
      j.status === 'running' ||
      j.status === 'awaiting_approval' ||
      j.status === 'uploading',
  )

  const { data: historyData, isLoading: historyLoading } = useQuery({
    queryKey: ['history-recent'],
    queryFn: () => api.getHistory({ limit: 5, status: 'success' }),
  })

  const recentCompleted = historyData?.items ?? []

  const { data: pendingData } = useQuery({
    queryKey: ['pending-uploads'],
    queryFn: () => api.getPendingUploads(),
    staleTime: 0,
  })

  const pendingUploads = pendingData?.pending_uploads ?? []

  const openPreview = useCallback((upload: PendingUpload) => {
    setPreviewState({
      uploadId: upload.upload_id,
      pendingUpload: upload,
      selectedImageIndex: upload.best_image_index,
      recipe: structuredClone(upload.recipe),
    })
  }, [])

  const shownPendingRef = useRef<Set<string>>(new Set())

  if (pendingUploads.length > 0 && !previewState) {
    const first = pendingUploads[0]
    if (!shownPendingRef.current.has(first.upload_id)) {
      shownPendingRef.current.add(first.upload_id)
      toast.info(`Recipe "${first.recipe.name || 'Untitled'}" is waiting for your confirmation!`)
      openPreview(first)
    }
  }

  const invalidateJobs = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['jobs'] })
  }, [queryClient])

  const handleJobProgress = useCallback(
    (_p: JobProgressPayload) => {
      invalidateJobs()
    },
    [invalidateJobs],
  )

  const handleJobComplete = useCallback(
    (p: JobCompletePayload) => {
      invalidateJobs()
      void queryClient.invalidateQueries({ queryKey: ['history-recent'] })
      toast.success(`Recipe "${p.recipe.name || 'Untitled'}" created successfully!`)
    },
    [invalidateJobs, queryClient],
  )

  const handleJobFailed = useCallback(
    (p: JobFailedPayload) => {
      invalidateJobs()
      toast.error(`Job failed: ${p.error}`)
    },
    [invalidateJobs],
  )

  const handleJobCancelled = useCallback(
    (_p: { job_id: string }) => {
      invalidateJobs()
      toast.info('Job cancelled')
    },
    [invalidateJobs],
  )

  const handleTransition = useCallback(
    (_p: JobTransitionPayload) => {
      invalidateJobs()
    },
    [invalidateJobs],
  )

  const handleRecipePreview = useCallback(
    (p: {
      job_id: string
      upload_id: string | null
      recipe: import('@/types').RecipeData
      image_data: string | null
      candidate_images: import('@/types').CandidateImage[]
      best_image_index: number
      output_target: string
      owner: string | null
    }) => {
      if (!p.upload_id) return
      const upload: PendingUpload = {
        upload_id: p.upload_id,
        job_id: p.job_id,
        recipe: p.recipe,
        output_target: p.output_target,
        best_image_index: p.best_image_index,
        selected_image_index: p.best_image_index,
        image_data: p.image_data ?? undefined,
        candidate_images: p.candidate_images,
      }
      openPreview(upload)
      invalidateJobs()
    },
    [openPreview, invalidateJobs],
  )

  useSocketEvent('job_progress', handleJobProgress)
  useSocketEvent('job_complete', handleJobComplete)
  useSocketEvent('job_failed', handleJobFailed)
  useSocketEvent('job_cancelled', handleJobCancelled)
  useSocketEvent('recipe_preview', handleRecipePreview)

  useTransitionEvent('running', handleTransition)
  useTransitionEvent('awaiting_approval', handleTransition)
  useTransitionEvent('uploading', handleTransition)
  useTransitionEvent('completed', handleTransition)
  useTransitionEvent('failed', handleTransition)
  useTransitionEvent('cancelled', handleTransition)
  useTransitionEvent('expired', handleTransition)

  const createJobMutation = useMutation({
    mutationFn: (url: string) => api.createJob(url),
    onSuccess: (data) => {
      invalidateJobs()
      setUrlInput('')
      toast.success(
        data.queue_position > 1
          ? `Job queued (position ${data.queue_position})`
          : 'Job started!',
      )
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const batchJobsMutation = useMutation({
    mutationFn: (urls: string[]) => api.createJobsBatch(urls),
    onSuccess: (data) => {
      invalidateJobs()
      setBatchInput('')
      toast.success(`Queued ${data.count} job(s)`)
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const cancelJobMutation = useMutation({
    mutationFn: (jobId: string) => api.cancelJob(jobId),
    onSuccess: () => {
      invalidateJobs()
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const confirmUploadMutation = useMutation({
    mutationFn: ({
      uploadId,
      selectedIndex,
      recipe,
    }: {
      uploadId: string
      selectedIndex: number
      recipe: RecipeData
    }) =>
      api.confirmPendingUpload(uploadId, {
        selected_image_index: selectedIndex,
        recipe,
      }),
    onSuccess: () => {
      setPreviewState(null)
      void queryClient.invalidateQueries({ queryKey: ['pending-uploads'] })
      invalidateJobs()
      toast.success('Upload confirmed!')
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const cancelUploadMutation = useMutation({
    mutationFn: (uploadId: string) => api.cancelPendingUpload(uploadId),
    onSuccess: () => {
      setPreviewState(null)
      void queryClient.invalidateQueries({ queryKey: ['pending-uploads'] })
      invalidateJobs()
      toast.info('Upload cancelled')
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const handleSubmit = useCallback(() => {
    const url = urlInput.trim()
    if (!url) {
      toast.error('Please enter a video URL')
      return
    }
    if (!isValidUrl(url)) {
      toast.error('Please enter a valid URL')
      return
    }
    createJobMutation.mutate(url)
  }, [urlInput, createJobMutation])

  const handleBatchSubmit = useCallback(() => {
    const raw = batchInput.trim()
    if (!raw) {
      toast.error('Enter at least one URL')
      return
    }
    const urls = raw
      .split(/[\n,]+/)
      .map((u) => u.trim())
      .filter(Boolean)
    if (urls.length > 50) {
      toast.error('Maximum 50 URLs per batch')
      return
    }
    batchJobsMutation.mutate(urls)
  }, [batchInput, batchJobsMutation])

  if (
    sharedUrl &&
    autoStart &&
    !autoStartFiredRef.current &&
    !createJobMutation.isPending
  ) {
    autoStartFiredRef.current = true
    if (isValidUrl(sharedUrl)) {
      createJobMutation.mutate(sharedUrl)
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 px-4 py-8">
      <div className="space-y-1 text-center">
        <h1 className="text-2xl font-bold">Pick-a-Recipe</h1>
        <p className="text-sm text-muted-foreground">
          Extract recipes from TikTok, YouTube, Instagram and more
        </p>
      </div>

      <div className="space-y-3">
        <div className="flex gap-2">
          <Input
            type="url"
            placeholder="Paste URL here (TikTok, YouTube, Instagram, or any recipe website…)"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSubmit()
            }}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            className="flex-1"
          />
          <Button
            onClick={handleSubmit}
            disabled={createJobMutation.isPending}
          >
            <SparklesIcon />
            Extract Recipe
          </Button>
        </div>

        <div className="rounded-lg border">
          <button
            type="button"
            onClick={() => setBatchOpen((o) => !o)}
            className="flex w-full items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:text-foreground"
          >
            <ChevronDownIcon
              className={`size-3.5 transition-transform ${batchOpen ? 'rotate-180' : ''}`}
            />
            Batch import (multiple URLs)
          </button>
          {batchOpen && (
            <div className="space-y-2 border-t px-3 pb-3 pt-2">
              <Textarea
                rows={4}
                placeholder="One URL per line (max 50)…"
                value={batchInput}
                onChange={(e) => setBatchInput(e.target.value)}
                className="w-full resize-none text-sm"
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={handleBatchSubmit}
                disabled={batchJobsMutation.isPending}
              >
                <LayersIcon />
                Queue All
              </Button>
            </div>
          )}
        </div>
      </div>

      {(activeJobs.length > 0 || jobsLoading) && (
        <section className="space-y-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            Active Jobs
            {!jobsLoading && (
              <span className="text-xs font-normal text-muted-foreground">
                ({activeJobs.length})
              </span>
            )}
          </h2>
          {jobsLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-28 w-full rounded-xl" />
              <Skeleton className="h-28 w-full rounded-xl" />
            </div>
          ) : (
            <div className="space-y-3">
              {activeJobs.map((job) => (
                <JobCard
                  key={job.id}
                  job={job}
                  onCancel={(id) => cancelJobMutation.mutate(id)}
                  href={`/jobs/${job.id}`}
                />
              ))}
            </div>
          )}
        </section>
      )}

      {(recentCompleted.length > 0 || historyLoading) && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">Recently Completed</h2>
          {historyLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-12 w-full rounded-lg" />
              <Skeleton className="h-12 w-full rounded-lg" />
            </div>
          ) : (
            <div className="space-y-2">
              {recentCompleted.map((entry) => (
                <div
                  key={entry.id}
                  className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2.5 text-sm"
                >
                  <CheckCircle2Icon className="size-4 shrink-0 text-emerald-500" />
                  <span className="flex-1 truncate font-medium">
                    {entry.recipe_name ?? entry.video_title ?? 'Untitled Recipe'}
                  </span>
                  <Link
                    to="/tasks"
                    className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
                  >
                    View
                  </Link>
                </div>
              ))}
            </div>
          )}
          <Link to="/tasks">
            <Button variant="secondary" size="sm">
              <ListChecksIcon />
              View All Tasks
            </Button>
          </Link>
        </section>
      )}

      <Dialog
        open={previewState !== null}
        onOpenChange={(open) => {
          if (!open) setPreviewState(null)
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Recipe Preview</DialogTitle>
            {previewState && (
              <DialogDescription>
                Review before uploading to{' '}
                <span className="font-medium text-foreground">
                  {previewState.pendingUpload.output_target}
                </span>
              </DialogDescription>
            )}
          </DialogHeader>

          {previewState && (
            <div className="space-y-4">
              {previewState.pendingUpload.candidate_images.length > 0 && (
                <ImagePicker
                  images={previewState.pendingUpload.candidate_images}
                  value={previewState.selectedImageIndex}
                  onChange={(i) =>
                    setPreviewState((s) =>
                      s ? { ...s, selectedImageIndex: i } : s,
                    )
                  }
                />
              )}
              <RecipeEditForm
                recipe={previewState.recipe}
                onChange={(recipe) =>
                  setPreviewState((s) => (s ? { ...s, recipe } : s))
                }
              />
            </div>
          )}

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                if (previewState) {
                  cancelUploadMutation.mutate(previewState.uploadId)
                }
              }}
              disabled={cancelUploadMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                if (previewState) {
                  confirmUploadMutation.mutate({
                    uploadId: previewState.uploadId,
                    selectedIndex: previewState.selectedImageIndex,
                    recipe: previewState.recipe,
                  })
                }
              }}
              disabled={confirmUploadMutation.isPending}
            >
              Confirm Upload
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
