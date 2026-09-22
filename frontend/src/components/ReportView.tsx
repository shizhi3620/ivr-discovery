import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { LocalizedOptimizationReport, OptimizationReport } from '../types';

interface ReportViewProps {
  sessionId: string | null;
  report: OptimizationReport | null;
  loading: boolean;
  error: string | null;
  canGenerate: boolean;
  businessContext: string;
  onBusinessContextChange: (value: string) => void;
  onGenerate: (force?: boolean) => void;
}

const severityStyles: Record<string, string> = {
  high: 'border-red-500/40 bg-red-500/10 text-red-300',
  medium: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  low: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
};

function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="border border-gray-800/80 rounded-xl bg-gray-950/40 overflow-hidden">
      <h2 className="px-4 py-3 text-sm font-semibold text-gray-200 border-b border-gray-800/80">
        {title}
      </h2>
      <div className="p-4">{children}</div>
    </section>
  );
}

function reportToMarkdown(report: OptimizationReport): string {
  const lines: string[] = [`# ${report.phone_number ?? 'IVR'} Optimization Report`, ''];

  for (const language of ['zh', 'en'] as const) {
    const section = report[language];
    lines.push(`# ${language === 'zh' ? '中文版' : 'English version'}`, '');
    lines.push(`## ${section.title}`, '', section.executive_summary, '');

    lines.push(`## ${language === 'zh' ? '时段路由' : 'Time-based routing'}`, '');
    section.time_routing.forEach((route) => {
      lines.push(
        `- **${route.window ?? ''}**: ${route.behavior ?? ''}`,
        `  - Evidence: ${route.evidence ?? ''}`,
        `  - Confidence: ${route.confidence ?? ''}`,
        `  - Recommendation: ${route.recommendation ?? ''}`
      );
    });
    lines.push('');

    lines.push(`## ${language === 'zh' ? '当前流程' : 'Current flow'}`, '');
    section.current_flow.forEach((node) => {
      lines.push(`### ${node.path || node.node_id || '-'}`, '', node.prompt ?? '');
      node.options?.forEach((option) => {
        lines.push(`- ${option.key ?? '?'}: ${option.label ?? ''}`);
      });
      if (node.observation) lines.push('', node.observation);
      lines.push('');
    });

    lines.push(`## ${language === 'zh' ? '问题与建议' : 'Issues and recommendations'}`, '');
    section.issues.forEach((issue) => {
      lines.push(
        `### [${(issue.severity ?? 'medium').toUpperCase()}] ${issue.finding ?? ''}`,
        '',
        `- Node: ${issue.node_id ?? '-'}`,
        `- Recommendation: ${issue.recommendation ?? ''}`,
        `- Expected impact: ${issue.expected_impact ?? ''}`,
        ''
      );
    });

    lines.push(`## ${language === 'zh' ? '建议流程' : 'Proposed flow'}`, '', section.proposed_flow, '');
    lines.push(`## ${language === 'zh' ? '指标' : 'Metrics'}`, '');
    section.metrics.forEach((metric) => lines.push(`- ${metric}`));
    lines.push('', `## ${language === 'zh' ? '验证计划' : 'Validation plan'}`, '');
    section.validation_plan.forEach((item) => lines.push(`- ${item}`));
    lines.push('', `## ${language === 'zh' ? '未验证项' : 'Unknown items'}`, '');
    section.unknown_items.forEach((item) => lines.push(`- ${item}`));
    lines.push('');
  }

  return lines.join('\n');
}

export function ReportView({
  sessionId,
  report,
  loading,
  error,
  canGenerate,
  businessContext,
  onBusinessContextChange,
  onGenerate,
}: ReportViewProps) {
  const [language, setLanguage] = useState<'zh' | 'en'>('zh');
  const section: LocalizedOptimizationReport | null = useMemo(
    () => (report ? report[language] : null),
    [report, language]
  );

  const handleExport = () => {
    if (!report) return;
    const blob = new Blob([reportToMarkdown(report)], {
      type: 'text/markdown;charset=utf-8',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${report.phone_number ?? 'ivr'}-optimization-report.md`;
    link.click();
    URL.revokeObjectURL(url);
  };

  if (!sessionId) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-gray-600">
        Run or restore a discovery session before generating a report.
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-6 flex flex-col gap-5">
        <div className="border border-gray-800/80 rounded-xl bg-gray-950/50 p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-lg font-semibold text-gray-100">
                {section?.title ?? 'IVR Optimization Report'}
              </h1>
              <p className="text-xs text-gray-500 mt-1">
                Bilingual report generated from this discovery session.
              </p>
            </div>
            <div className="flex items-center gap-2">
              {report && (
                <>
                  <div className="flex rounded-lg border border-gray-800 p-0.5">
                    {(['zh', 'en'] as const).map((code) => (
                      <button
                        key={code}
                        onClick={() => setLanguage(code)}
                        className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
                          language === code
                            ? 'bg-indigo-600 text-white'
                            : 'text-gray-400 hover:text-gray-200'
                        }`}
                      >
                        {code === 'zh' ? '中文' : 'English'}
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={handleExport}
                    className="px-3 py-1.5 text-xs rounded-lg border border-gray-700 text-gray-300 hover:bg-gray-800"
                  >
                    Export MD
                  </button>
                  <button
                    disabled={!canGenerate}
                    onClick={() => onGenerate(true)}
                    className="px-3 py-1.5 text-xs rounded-lg border border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10 disabled:opacity-40"
                  >
                    Regenerate
                  </button>
                </>
              )}
            </div>
          </div>

          <label className="block text-xs text-gray-500 mt-4 mb-2">
            Business context used by the report generator
          </label>
          <textarea
            value={businessContext}
            onChange={(event) => onBusinessContextChange(event.target.value)}
            rows={4}
            className="w-full rounded-lg border border-gray-800 bg-[#030712] px-3 py-2 text-sm text-gray-200 outline-none focus:border-indigo-500/60"
            placeholder="Example: Before 21:00 calls route to human agents; after 21:00 calls use IVR self-service."
          />
          <div className="flex items-center gap-3 mt-3">
            <button
              disabled={loading || !canGenerate}
              onClick={() => onGenerate(false)}
              className="px-4 py-2 text-sm rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white"
            >
              {loading ? 'Generating…' : report ? 'Generate from updated context' : 'Generate report'}
            </button>
            {!canGenerate && (
              <span className="text-xs text-amber-400">
                Wait until the full IVR discovery finishes before generating the final report.
              </span>
            )}
            {error && <span className="text-xs text-red-400">{error}</span>}
          </div>
        </div>

        {section && (
          <>
            <Section title={language === 'zh' ? '执行摘要' : 'Executive summary'}>
              <p className="text-sm leading-6 text-gray-300 whitespace-pre-line">
                {section.executive_summary}
              </p>
            </Section>

            <Section title={language === 'zh' ? '时段路由' : 'Time-based routing'}>
              <div className="grid gap-3">
                {section.time_routing.map((route, index) => (
                  <div
                    key={`${route.window}-${index}`}
                    className="rounded-lg border border-gray-800 bg-black/20 p-3"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-gray-200">
                        {route.window}
                      </span>
                      <span className="text-[11px] text-gray-500">
                        {route.confidence}
                      </span>
                    </div>
                    <p className="text-sm text-gray-300 mt-2">{route.behavior}</p>
                    <p className="text-xs text-gray-500 mt-2">
                      {route.evidence}
                    </p>
                    {route.recommendation && (
                      <p className="text-xs text-indigo-300 mt-2">
                        {route.recommendation}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </Section>

            <Section title={language === 'zh' ? '当前流程' : 'Current flow'}>
              <div className="flex flex-col gap-4">
                {section.current_flow.map((node, index) => (
                  <div
                    key={`${node.node_id}-${index}`}
                    className="border-l-2 border-gray-800 pl-4"
                  >
                    <div className="text-xs text-gray-500 font-mono">
                      {node.path || node.node_id || '-'}
                    </div>
                    <p className="text-sm text-gray-200 mt-1 whitespace-pre-line">
                      {node.prompt}
                    </p>
                    {node.options && node.options.length > 0 && (
                      <div className="flex flex-wrap gap-2 mt-2">
                        {node.options.map((option, optionIndex) => (
                          <span
                            key={`${option.key}-${optionIndex}`}
                            className="text-xs rounded-md border border-gray-800 bg-gray-900 px-2 py-1 text-gray-300"
                          >
                            {option.key}: {option.label}
                          </span>
                        ))}
                      </div>
                    )}
                    {node.observation && (
                      <p className="text-xs text-gray-500 mt-2">{node.observation}</p>
                    )}
                  </div>
                ))}
              </div>
            </Section>

            <Section title={language === 'zh' ? '问题与建议' : 'Issues and recommendations'}>
              <div className="flex flex-col gap-3">
                {section.issues.map((issue, index) => {
                  const severity = (issue.severity ?? 'medium').toLowerCase();
                  return (
                    <div
                      key={`${issue.finding}-${index}`}
                      className={`rounded-lg border p-3 ${
                        severityStyles[severity] ?? severityStyles.medium
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-xs font-semibold uppercase">
                          {severity}
                        </span>
                        <span className="text-[11px] font-mono opacity-70">
                          {issue.node_id ?? '-'}
                        </span>
                      </div>
                      <p className="text-sm text-gray-200 mt-2">{issue.finding}</p>
                      <p className="text-xs text-gray-300 mt-2">
                        {issue.recommendation}
                      </p>
                      {issue.expected_impact && (
                        <p className="text-xs text-gray-500 mt-1">
                          {issue.expected_impact}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            </Section>

            <Section title={language === 'zh' ? '建议流程' : 'Proposed flow'}>
              <p className="text-sm leading-6 text-gray-300 whitespace-pre-line">
                {section.proposed_flow}
              </p>
            </Section>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <Section title={language === 'zh' ? '指标' : 'Metrics'}>
                <ul className="text-sm text-gray-300 space-y-2">
                  {section.metrics.map((metric) => (
                    <li key={metric}>• {metric}</li>
                  ))}
                </ul>
              </Section>
              <Section title={language === 'zh' ? '验证计划' : 'Validation plan'}>
                <ul className="text-sm text-gray-300 space-y-2">
                  {section.validation_plan.map((item) => (
                    <li key={item}>• {item}</li>
                  ))}
                </ul>
              </Section>
              <Section title={language === 'zh' ? '未验证项' : 'Unknown items'}>
                <ul className="text-sm text-gray-300 space-y-2">
                  {section.unknown_items.map((item) => (
                    <li key={item}>• {item}</li>
                  ))}
                </ul>
              </Section>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
