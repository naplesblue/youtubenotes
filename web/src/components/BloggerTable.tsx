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
      header: '分析师',
      cell: ({ row }) => (
        <a href={`/bloggers/${row.original.slug}/`} className="data-link font-medium">
          {row.original.analyst}
        </a>
      ),
    },
    {
      accessorKey: 'channel',
      header: '频道',
      cell: ({ getValue }) => (
        <span style={{ color: 'var(--color-text-muted)' }}>{getValue() as string}</span>
      ),
    },
    {
      accessorKey: 'total_opinions',
      header: '观点数',
      cell: ({ getValue }) => <span className="font-data">{getValue() as number}</span>,
    },
    {
      id: 'win_rate_30d',
      header: '30天胜率',
      accessorFn: (row) => row.win_rate?.['30d'],
      cell: ({ getValue }) => {
        const v = getValue() as number | null;
        return v != null
          ? <span className="font-data">{(v * 100).toFixed(0)}%</span>
          : <span className="badge badge-amber">待验证</span>;
      },
    },
    {
      id: 'win_rate_90d',
      header: '90天胜率',
      accessorFn: (row) => row.win_rate?.['90d'],
      cell: ({ getValue }) => {
        const v = getValue() as number | null;
        return v != null
          ? <span className="font-data">{(v * 100).toFixed(0)}%</span>
          : <span className="badge badge-amber">待验证</span>;
      },
    },
    {
      accessorKey: 'credibility_score',
      header: '信誉分',
      cell: ({ getValue }) => {
        const v = getValue() as number | null;
        return v != null
          ? <span className="font-data">{v.toFixed(1)}</span>
          : <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
      },
    },
    {
      id: 'top_tickers',
      header: '热门标的',
      enableSorting: false,
      cell: ({ row }) => (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {row.original.top_tickers.slice(0, 5).map(t => (
            <a key={t.ticker} href={`/tickers/${t.ticker}/`} className="pill">
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
    <div style={{ overflowX: 'auto' }}>
      <table className="data-table">
        <thead>
          {table.getHeaderGroups().map(hg => (
            <tr key={hg.id}>
              {hg.headers.map(header => (
                <th key={header.id}
                    style={{ cursor: header.column.getCanSort() ? 'pointer' : 'default' }}
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
            <tr key={row.id}>
              {row.getVisibleCells().map(cell => (
                <td key={cell.id}>
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
