import { useState, useMemo } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  type SortingState,
  type ColumnDef,
  flexRender,
} from '@tanstack/react-table';
import type { Opinion } from '../lib/types';
import { typeLabels, directionLabels, confidenceLabels, horizonLabels } from '../lib/colors';

interface Props {
  opinions: Opinion[];
  showChannel?: boolean;
}

function SentimentBadge({ sentiment }: { sentiment: string }) {
  const cls = sentiment.includes('bullish')
    ? 'bg-green-500/20 text-green-400'
    : sentiment.includes('bearish')
    ? 'bg-red-500/20 text-red-400'
    : 'bg-slate-500/20 text-slate-400';
  const label = sentiment.includes('bullish') ? '多' : sentiment.includes('bearish') ? '空' : '中';
  return <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${cls}`}>{label}</span>;
}

export default function OpinionTable({ opinions, showChannel = false }: Props) {
  const [sorting, setSorting] = useState<SortingState>([{ id: 'published_date', desc: true }]);
  const [globalFilter, setGlobalFilter] = useState('');

  const columns = useMemo<ColumnDef<Opinion>[]>(() => {
    const cols: ColumnDef<Opinion>[] = [];
    if (showChannel) {
      cols.push({
        accessorKey: 'analyst',
        header: '分析师',
        cell: ({ row }) => (
          <a href={`/bloggers/${encodeURIComponent(row.original.analyst)}/`}
             className="text-blue-400 hover:text-blue-300 text-sm">
            {row.original.analyst}
          </a>
        ),
      });
    }
    cols.push(
      {
        accessorKey: 'sentiment',
        header: '方向',
        cell: ({ row }) => (
          <div className="flex items-center gap-1.5">
            <SentimentBadge sentiment={row.original.sentiment} />
            <span className="text-xs text-slate-400">
              {directionLabels[row.original.prediction.direction] || row.original.prediction.direction}
            </span>
          </div>
        ),
      },
      {
        id: 'type',
        header: '类型',
        accessorFn: (row) => row.prediction.type,
        cell: ({ getValue }) => (
          <span className="text-xs text-slate-300">{typeLabels[getValue() as string] || getValue() as string}</span>
        ),
      },
      {
        id: 'price',
        header: '入场价',
        accessorFn: (row) => row.prediction.price,
        cell: ({ getValue }) => {
          const v = getValue() as number | null;
          return <span className="font-mono text-sm">{v != null ? `$${v.toFixed(1)}` : '—'}</span>;
        },
      },
      {
        id: 'target_price',
        header: '目标价',
        accessorFn: (row) => row.prediction.target_price,
        cell: ({ getValue }) => {
          const v = getValue() as number | null;
          return <span className="font-mono text-sm">{v != null ? `$${v.toFixed(1)}` : '—'}</span>;
        },
      },
      {
        id: 'stop_loss',
        header: '止损',
        accessorFn: (row) => row.prediction.stop_loss,
        cell: ({ getValue }) => {
          const v = getValue() as number | null;
          return <span className="font-mono text-sm">{v != null ? `$${v.toFixed(1)}` : '—'}</span>;
        },
      },
      {
        id: 'confidence',
        header: '置信',
        accessorFn: (row) => row.prediction.confidence,
        cell: ({ getValue }) => (
          <span className="text-xs">{confidenceLabels[getValue() as string] || getValue() as string}</span>
        ),
      },
      {
        accessorKey: 'published_date',
        header: '日期',
        cell: ({ getValue }) => <span className="text-xs text-slate-400">{(getValue() as string).slice(5)}</span>,
      },
    );
    return cols;
  }, [showChannel]);

  const table = useReactTable({
    data: opinions,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  return (
    <div>
      {opinions.length > 10 && (
        <input
          type="text"
          placeholder="筛选..."
          value={globalFilter}
          onChange={e => setGlobalFilter(e.target.value)}
          className="mb-3 px-3 py-1.5 rounded-lg text-sm border border-slate-700 bg-slate-800 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500 w-48"
        />
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            {table.getHeaderGroups().map(hg => (
              <tr key={hg.id} className="border-b border-slate-700">
                {hg.headers.map(header => (
                  <th key={header.id}
                      className="text-left py-2 px-2 text-slate-400 font-medium text-xs cursor-pointer select-none hover:text-slate-200"
                      onClick={header.column.getToggleSortingHandler()}>
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {{ asc: ' ↑', desc: ' ↓' }[header.column.getIsSorted() as string] ?? ''}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map(row => (
              <tr key={row.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                {row.getVisibleCells().map(cell => (
                  <td key={cell.id} className="py-2 px-2">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
