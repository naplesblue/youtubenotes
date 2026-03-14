import { useState, useMemo } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  type ColumnDef,
  flexRender,
} from '@tanstack/react-table';
import type { Blogger } from '../lib/types';

interface Props {
  bloggers: Blogger[];
}

export default function BloggerTable({ bloggers }: Props) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'total_opinions', desc: true },
  ]);

  const columns = useMemo<ColumnDef<Blogger>[]>(() => [
    {
      accessorKey: 'analyst',
      header: '博主',
      cell: ({ row }) => (
        <a href={`/bloggers/${encodeURIComponent(row.original.analyst)}/`}
           className="text-blue-400 hover:text-blue-300 font-medium">
          {row.original.analyst}
        </a>
      ),
    },
    {
      accessorKey: 'channel',
      header: '频道',
      cell: ({ getValue }) => <span className="text-slate-400 text-sm">{getValue() as string}</span>,
    },
    {
      accessorKey: 'total_opinions',
      header: '观点数',
      cell: ({ getValue }) => <span className="font-mono">{getValue() as number}</span>,
    },
    {
      id: 'win_rate_30d',
      header: '30d 胜率',
      accessorFn: (row) => row.win_rate?.['30d'],
      cell: ({ getValue }) => {
        const v = getValue() as number | null;
        return v != null
          ? <span className="font-mono">{(v * 100).toFixed(0)}%</span>
          : <span className="text-xs px-2 py-0.5 rounded bg-amber-500/15 text-amber-400">pending</span>;
      },
    },
    {
      id: 'win_rate_90d',
      header: '90d 胜率',
      accessorFn: (row) => row.win_rate?.['90d'],
      cell: ({ getValue }) => {
        const v = getValue() as number | null;
        return v != null
          ? <span className="font-mono">{(v * 100).toFixed(0)}%</span>
          : <span className="text-xs px-2 py-0.5 rounded bg-amber-500/15 text-amber-400">pending</span>;
      },
    },
    {
      accessorKey: 'credibility_score',
      header: '信誉分',
      cell: ({ getValue }) => {
        const v = getValue() as number | null;
        return v != null
          ? <span className="font-mono">{v.toFixed(1)}</span>
          : <span className="text-slate-500">—</span>;
      },
    },
    {
      id: 'top_tickers',
      header: '热门 Ticker',
      enableSorting: false,
      cell: ({ row }) => (
        <div className="flex gap-1 flex-wrap">
          {row.original.top_tickers.slice(0, 5).map(t => (
            <a key={t.ticker} href={`/tickers/${t.ticker}/`}
               className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 hover:bg-slate-600">
              {t.ticker}
            </a>
          ))}
        </div>
      ),
    },
  ], []);

  const table = useReactTable({
    data: bloggers,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          {table.getHeaderGroups().map(hg => (
            <tr key={hg.id} className="border-b border-slate-700">
              {hg.headers.map(header => (
                <th key={header.id}
                    className="text-left py-3 px-3 text-slate-400 font-medium cursor-pointer select-none hover:text-slate-200"
                    onClick={header.column.getToggleSortingHandler()}>
                  <div className="flex items-center gap-1">
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {{ asc: ' ↑', desc: ' ↓' }[header.column.getIsSorted() as string] ?? ''}
                  </div>
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map(row => (
            <tr key={row.id} className="border-b border-slate-800 hover:bg-slate-800/50 transition-colors">
              {row.getVisibleCells().map(cell => (
                <td key={cell.id} className="py-3 px-3">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
