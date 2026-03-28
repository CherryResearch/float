import React from 'react';
import { render, fireEvent, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import ThreadsModule from '../ThreadsModule';

const threads = [
  { id: 't1', title: 'Thread A', metadata: '2 msgs', content: 'Thread details' },
];

describe('ThreadsModule', () => {
  it('toggles thread content', () => {
    render(<ThreadsModule threads={threads} />);
    fireEvent.click(screen.getByText('Thread A'));
    expect(screen.getByTestId('thread-details')).toHaveTextContent('Thread details');
  });
});

