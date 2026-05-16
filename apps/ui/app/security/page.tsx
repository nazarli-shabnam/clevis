"use client"

import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Shield, AlertTriangle, CheckCircle, XCircle } from "lucide-react"
import { api } from "@/lib/api/client"
import type { CheckResult } from "@/lib/api/types"

export default function SecurityPage() {
  const [owner, setOwner] = useState("")
  const [token, setToken] = useState("")

  const scan = useMutation({
    mutationFn: () => api.analytics.overview(owner, token),
  })

  return (
    <>
      <PageHeader
        title="Health & Security"
        description="Run security checks for a GitHub organization and inspect its posture."
      />

      <div className="grid gap-6 md:grid-cols-2">
        <Card className="glow-border">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="size-5 text-primary" />
              Run Scan
            </CardTitle>
            <CardDescription>
              Enter your organization login and a GitHub token to analyze security posture.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <Input
              placeholder="Organization login"
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
            />
            <Input
              placeholder="GitHub token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
            <Button onClick={() => scan.mutate()} disabled={scan.isPending || !owner || !token}>
              {scan.isPending ? "Scanning..." : "Run analytics"}
            </Button>
            {scan.isError && (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <AlertTriangle className="size-4" />
                {scan.error.message}
              </div>
            )}
          </CardContent>
        </Card>

        {scan.data && (
          <Card className="glow-border">
            <CardHeader>
              <CardTitle>Score</CardTitle>
              <CardDescription>
                {scan.data.failed_checks} of {scan.data.total_checks} checks failed
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-center">
                <span className={`text-6xl font-bold ${scan.data.score >= 80 ? "text-accent" : scan.data.score >= 50 ? "text-chart-4" : "text-destructive"}`}>
                  {scan.data.score}
                </span>
                <span className="ml-1 text-2xl text-muted-foreground">/100</span>
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {scan.data && (
        <Card className="mt-6 glow-border">
          <CardHeader>
            <CardTitle>Check Results</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="divide-y divide-border/50">
              {scan.data.checks.map((c: CheckResult) => (
                <div key={c.id} className="flex items-start gap-4 py-4 first:pt-0 last:pb-0">
                  {c.status === "pass" ? (
                    <CheckCircle className="mt-0.5 size-5 shrink-0 text-accent" />
                  ) : (
                    <XCircle className="mt-0.5 size-5 shrink-0 text-destructive" />
                  )}
                  <div className="flex-1 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{c.title}</span>
                      <Badge variant={c.severity === "high" ? "destructive" : "secondary"}>
                        {c.severity}
                      </Badge>
                    </div>
                    <p className="text-sm text-muted-foreground">{c.remediation}</p>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </>
  )
}
