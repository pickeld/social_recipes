import { useCallback, useEffect, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { HomeIcon, ListChecksIcon } from 'lucide-react'
import { JobCard } from '@/components/job-card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Card, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
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
import { api } from '@/lib/api'
import { useJobRoom, useSocketEvent, useTransitionEvent } from '@/lib/socket'
import type { Job, JobProgressPayload, JobCompletePayload, JobFailedPayload, JobTransitionPayload } from '@/types'

function formatTimestamp(raw: string): string {
  try {
    return new Date(raw.replace(' ', 'T')).toLocaleString()
  } catch {
    return raw
  }
}

export function JobPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [cancelling, setCancelling] = useState(false)
  const [retrying, setRetrying] = useState(false)

  const queryKey = ['job', jobId]

  const { data: job, isLoading, isError, error } = useQuery<Job>({
    queryKey,
    queryFn: () => api.getJob(jobId!),
    enabled: !!jobId,
    retry: (count, err) => {
      if (err instanceof Error && 'status' in err && (err as { status: number }).status === 404) {
        return false
      }
      return count < 2
    },
  })

  useEffect(() => {
    if (job?.video_title) {
      document.title = `${job.video_title} — Pick-a-Recipe`
    } else {
      document.title = 'Job Progress — Pick-a-Recipe'
    }
    return () => {
      document.title = 'Pick-a-Recipe'
    }
  }, [job?.video_title])

  useJobRoom(jobId)

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey })
  }, [queryClient, queryKey])

  useSocketEvent(
    'job_progress',
    useCallback(
      (data: JobProgressPayload) => {
        if (data.job_id === jobId) invalidate()
      },
      [jobId, invalidate],
    ),
  )

  useSocketEvent(
    'job_complete',
    useCallback(
      (data: JobCompletePayload) => {
        if (data.job_id === jobId) invalidate()
      },
      [jobId, invalidate],
    ),
  )

  useSocketEvent(
    'job_failed',
    useCallback(
      (data: JobFailedPayload) => {
        if (data.job_id === jobId) invalidate()
      },
      [jobId, invalidate],
    ),
  )

  useSocketEvent(
    'job_cancelled',
    useCallback(
      (data: { job_id: string }) => {
        if (data.job_id === jobId) invalidate()
      },
      [jobId, invalidate],
    ),
  )

  const handleTransition = useCallback(
    (data: JobTransitionPayload) => {
      if (data.job_id === jobId) invalidate()
    },
    [jobId, invalidate],
  )

  useTransitionEvent('running', handleTransition)
  useTransitionEvent('awaiting_approval', handleTransition)
  useTransitionEvent('uploading', handleTransition)
  useTransitionEvent('completed', handleTransition)
  useTransitionEvent('expired', handleTransition)

  const handleCancel = useCallback(async () => {
    if (!jobId) return
    setCancelling(true)
    try {
      await api.cancelJob(jobId)
      toast.success('Job cancelled')
      invalidate()
    } catch {
      toast.error('Failed to cancel job')
    } finally {
      setCancelling(false)
    }
  }, [jobId, invalidate])

  const handleRetry = useCallback(async () => {
    if (!job) return
    setRetrying(true)
    try {
      const data = await api.retryJob({ url: job.url })
      toast.success('Retry started')
      navigate(`/jobs/${data.job_id}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Retry failed')
    } finally {
      setRetrying(false)
    }
  }, [job, navigate])

  const is404 =
    isError &&
    error instanceof Error &&
    'status' in error &&
    (error as { status: number }).status === 404

  if (!jobId) {
    void navigate('/')
    return null
  }

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-8 space-y-6">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link to="/">
            <HomeIcon className="size-4" />
            Home
          </Link>
        </Button>
        <Button variant="ghost" size="sm" asChild>
          <Link to="/tasks">
            <ListChecksIcon className="size-4" />
            Tasks
          </Link>
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-xl" />
        </div>
      )}

      {is404 && (
        <Card>
          <CardHeader>
            <CardTitle>Job not found</CardTitle>
            <CardDescription>
              Job <span className="font-mono text-xs">{jobId}</span> does not exist or has been
              removed.{' '}
              <Link to="/" className="underline underline-offset-4 hover:text-foreground">
                Go home
              </Link>
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {isError && !is404 && (
        <Card>
          <CardHeader>
            <CardTitle>Failed to load job</CardTitle>
            <CardDescription>{error instanceof Error ? error.message : 'Unknown error'}</CardDescription>
          </CardHeader>
        </Card>
      )}

      {job && (
        <>
          <AlertDialog>
            <JobCard
              job={job}
              onCancel={
                cancelling
                  ? undefined
                  : () => {
                      document.getElementById('cancel-trigger')?.click()
                    }
              }
              onRetry={
                job.status === 'failed' && !retrying
                  ? () => {
                      void handleRetry()
                    }
                  : undefined
              }
            />
            <AlertDialogTrigger id="cancel-trigger" className="sr-only" />
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Cancel this job?</AlertDialogTitle>
                <AlertDialogDescription>
                  The job will be stopped and cannot be resumed. You can start a new job from the
                  same URL if needed.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Keep running</AlertDialogCancel>
                <AlertDialogAction variant="destructive" onClick={() => void handleCancel()}>
                  Cancel job
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <p className="text-xs text-muted-foreground text-center">
            Created {formatTimestamp(job.created_at)}
            {job.updated_at !== job.created_at && (
              <> &middot; Updated {formatTimestamp(job.updated_at)}</>
            )}
          </p>
        </>
      )}
    </div>
  )
}
