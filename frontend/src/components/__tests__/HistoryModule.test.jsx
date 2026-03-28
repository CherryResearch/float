import React from 'react';
import { render, fireEvent, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import HistoryModule from '../HistoryModule';

const items = [
  { id: '1', title: 'Chat 1', date: '2024-01-01', details: 'details 1' },
];

describe('HistoryModule', () => {
  it('expands item on click', () => {
    render(<HistoryModule items={items} />);
    fireEvent.click(screen.getByText('Chat 1'));
    expect(screen.getByTestId('history-details')).toHaveTextContent('details 1');
  });
});

